import logging
import os
import time
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile

import gphoto2 as gp

from ...utils.helper import filename_str_time
from ..config.groups.cameras import Gphoto2Parameters, GroupCameraGphoto2
from .abstractbackend import AbstractBackend, StillRequest

logger = logging.getLogger(__name__)


class GpWidgets(Enum):
    GP_WIDGET_WINDOW = 0
    GP_WIDGET_SECTION = 1
    GP_WIDGET_TEXT = 2
    GP_WIDGET_RANGE = 3
    GP_WIDGET_TOGGLE = 4
    GP_WIDGET_RADIO = 5
    GP_WIDGET_MENU = 6
    GP_WIDGET_BUTTON = 7
    GP_WIDGET_DATE = 8


# generate dict for clear events clear text names
# defined events http://www.gphoto.org/doc/api/gphoto2-camera_8h.html#a438ab2ac60ad5d5ced30e4201476800b (docs outdated!)
class GpEvents(Enum):
    GP_EVENT_UNKNOWN = 0
    GP_EVENT_TIMEOUT = 1
    GP_EVENT_FILE_ADDED = 2
    GP_EVENT_FOLDER_ADDED = 3
    GP_EVENT_CAPTURE_COMPLETE = 4
    GP_EVENT_FILE_CHANGED = 5


# assert to ensure our definition is always the same as in libgphoto
assert {e.name: e.value for e in GpWidgets} == {name: getattr(gp, name) for name in GpWidgets.__members__}, "GP_WIDGET are inconsistent"
assert {e.name: e.value for e in GpEvents} == {name: getattr(gp, name) for name in GpEvents.__members__}, "GP_EVENT are inconsistent"


class Gphoto2Backend(AbstractBackend):
    def __init__(self, config: GroupCameraGphoto2):
        self._config: GroupCameraGphoto2 = config
        super().__init__(
            config.orientation,
            num_subdevices=1,
            idle_timeout=self._config.camera_standby_when_inactive_time if self._config.camera_standby_when_inactive else None,
        )

        if gp is None:
            raise ModuleNotFoundError("Backend is not available - either wrong platform or not installed!")

        self._camera = gp.Camera()  # pyright: ignore [reportAttributeAccessIssue]
        self._camera_context = gp.Context()  # pyright: ignore [reportAttributeAccessIssue]
        self._pending_physical_capture: tuple[str, str] | None = None
        self._processed_camera_files: set[str] = set()
        self._last_job_completed_time: float = 0.0
        self._capture_busy: bool = False
        self._capture_started_at: float = 0.0
        self._desired_ui_locked: bool = False
        self._ui_locked: bool | None = None
        self._drain_requested: bool = False
        # Canon bodies need a few seconds after enabling Live View before the
        # first EVF frame is available. Do not mistake that warm-up period for
        # a broken USB connection.
        self._liveview_grace_until: float = 0.0

        logger.info(f"python-gphoto2: {gp.__version__}")
        logger.info(f"libgphoto2: {gp.gp_library_version(gp.GP_VERSION_VERBOSE)}")  # pyright: ignore [reportAttributeAccessIssue]
        logger.info(f"libgphoto2_port: {gp.gp_port_library_version(gp.GP_VERSION_VERBOSE)}")  # pyright: ignore [reportAttributeAccessIssue]

        # enable logging to python. need to store callback, otherwise logging does not work.
        # gphoto2 logging is too verbose, reduce mapping
        self._logger_callback = gp.check_result(
            gp.use_python_logging(
                mapping={
                    gp.GP_LOG_ERROR: logging.INFO,  # pyright: ignore [reportAttributeAccessIssue]
                    gp.GP_LOG_DEBUG: logging.DEBUG - 1,  # pyright: ignore [reportAttributeAccessIssue]
                    gp.GP_LOG_VERBOSE: logging.DEBUG - 3,  # pyright: ignore [reportAttributeAccessIssue]
                    gp.GP_LOG_DATA: logging.DEBUG - 6,  # pyright: ignore [reportAttributeAccessIssue]
                }
            )
        )

    def notify_job_completed(self):
        logger.info("📸 Collage job completed. Draining camera events and setting debounce.")
        try:
            from ...container import container
            from ..gallery_folders import gallery_folder_store

            job = container.processing_service._workflow_jobmodel
            if job is not None:
                media_ids = [
                    str(item.id)
                    for item in (getattr(job, "_result_mediaitems", None) or [])
                    if getattr(item, "id", None)
                ]
                active_folder_id = gallery_folder_store.snapshot().get("active_id")
                if media_ids and active_folder_id:
                    gallery_folder_store.assign(media_ids, active_folder_id)
        except Exception as exc:
            logger.warning("Could not assign completed capture to active gallery folder: %s", exc)
        self._last_job_completed_time = time.time()
        self._drain_requested = True

    @property
    def capture_busy(self) -> bool:
        """True only while a shutter event is being downloaded/prepared."""
        return self._capture_busy

    @property
    def capture_busy_ms(self) -> int:
        if not self._capture_busy or not self._capture_started_at:
            return 0
        return max(0, int((time.monotonic() - self._capture_started_at) * 1000))

    @property
    def shutter_locked(self) -> bool:
        return bool(self._desired_ui_locked)

    def _begin_capture_transfer(self) -> None:
        self._capture_busy = True
        self._capture_started_at = time.monotonic()
        self._desired_ui_locked = True

    def _finish_capture_transfer(self) -> None:
        elapsed_ms = self.capture_busy_ms
        self._capture_busy = False
        self._capture_started_at = 0.0
        logger.info(f"Canon image transfer ready for review in {elapsed_ms}ms")

    def _apply_canon_ui_lock(self, force: bool = False) -> None:
        """Apply the desired Canon body-button lock from the camera thread."""
        if not self._camera or (not force and self._ui_locked is self._desired_ui_locked):
            return
        try:
            cfg = self._camera.get_config()
            ui_lock = cfg.get_child_by_name("uilock")
            ui_lock.set_value(1 if self._desired_ui_locked else 0)
            self._camera.set_config(cfg)
            self._ui_locked = self._desired_ui_locked
            logger.info("Canon shutter controls %s", "locked while photo is reviewed" if self._ui_locked else "unlocked and ready")
        except Exception as exc:
            # Some Canon firmwares expose the action but reject it while the
            # card is still writing. Keep the desired state so the next loop
            # retries without blocking delivery of the preview image.
            self._ui_locked = None
            logger.debug(f"Canon UI lock deferred: {exc}")

    def _drain_events(self, timeout_ms: int = 50):
        try:
            while True:
                evt_t, evt_d = self._camera.wait_for_event(timeout_ms)
                if evt_t == gp.GP_EVENT_TIMEOUT:
                    break
                if evt_t == gp.GP_EVENT_FILE_ADDED:
                    base_name = os.path.splitext(evt_d.name)[0]
                    self._processed_camera_files.add(base_name)
                    logger.info(f"Drained and ignored extra burst/stale camera event: {evt_d.name}")
        except Exception:
            pass

    def start(self):
        super().start()

    def stop(self):
        super().stop()

    def _handle_switchmode_init(self):
        logger.debug("configure camera during device init")
        self._handle_switchmode(self._config.parameterset_device_init)

    def _handle_switchmode_video_mode(self):
        logger.debug("configure camera optimized for video")
        self._handle_switchmode(self._config.parameterset_video)

    def _handle_switchmode_still_mode(self):
        logger.debug("configure camera optimized for still capture (keep viewfinder active)")
        pass

    def _handle_switchmode_standby(self):
        logger.debug("configure camera optimized for standby (keep viewfinder active)")
        pass

    def _handle_switchmode(self, parameterset: list[Gphoto2Parameters]) -> None:
        if not parameterset or not any(cfg.enabled for cfg in parameterset):
            return

        camera_config = self._camera.get_config()

        for cfg in parameterset:
            if cfg.enabled:
                try:
                    self._update_camera_config(camera_config, cfg.name, cfg.value)
                except Exception as exc:
                    logger.warning(f"Error setting config: {exc}. It will be ignored.")

        self._camera.set_config(camera_config)  # ... and write all at once to camera

    def _update_camera_config(self, camera_config, name: str, value_new: str | int | bool | float):
        assert gp

        if name == "" or value_new == "":
            raise ValueError(f"setting is missing name '{name}' or value '{value_new}'")

        child = camera_config.get_child_by_name(name)
        is_readonly = child.get_readonly()
        typ = child.get_type()
        name = child.get_name()
        value_old = child.get_value()

        if is_readonly:
            raise PermissionError(f"'{name}' is readonly!")

        # update configuration object and properly cast the new value
        config_type = GpWidgets(typ)
        if config_type is GpWidgets.GP_WIDGET_TEXT:
            value_new_casted = str(value_new)
        elif config_type in (GpWidgets.GP_WIDGET_RADIO, GpWidgets.GP_WIDGET_MENU):
            # radio/menu widgets allow only specific values, so it allows to validate the user provided data before it's sent
            # to the camera and breaks everything. The available choices reported by the camera may differ by the selected mode
            available_choices = [choice for choice in child.get_choices()]

            if value_new not in available_choices:
                raise ValueError(f"{value_new} is not an option for '{name}' in current mode. Available choices are {available_choices}")

            value_new_casted = str(value_new)
        elif config_type is GpWidgets.GP_WIDGET_TOGGLE:
            value_new_casted = 1 if str(value_new).lower() in ("true", "1") else 0
        else:
            raise RuntimeError(f"The app does not support the setting's type '{config_type}' for {child.get_name()}")

        try:
            child.set_value(value_new_casted)  # update the python object ...
            logger.debug(f"Camera setting '{name}' changed from '{value_old}' to '{value_new}' ({value_new_casted})")
        except Exception as exc:
            logger.exception(exc)
            raise RuntimeError(f"Cannot set '{name}' to '{value_new}'! Command ignored. Error: {exc}") from exc

    def setup_resource(self):
        assert gp

        import subprocess, sys, threading
        if sys.platform == "darwin":
            if not getattr(self, "_ptp_killer_thread", None) or not self._ptp_killer_thread.is_alive():
                self._ptp_killer_stop = threading.Event()
                def _ptp_killer():
                    while not self._ptp_killer_stop.is_set():
                        try:
                            subprocess.run(["killall", "-9", "ptpcamerad"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception:
                            pass
                        time.sleep(0.5)
                self._ptp_killer_thread = threading.Thread(target=_ptp_killer, daemon=True)
                self._ptp_killer_thread.start()
            try:
                subprocess.run(["killall", "-9", "ptpcamerad"], capture_output=True)
            except Exception:
                pass

        # try open cam. if fails it raises an exception and the supvervisor tries to restart.
        # better use fresh object.
        self._camera = None
        cam = gp.Camera()  # pyright: ignore [reportAttributeAccessIssue]
        try:
            cam.init()
            self._camera = cam
        except Exception as exc:
            try:
                cam.exit()
            except Exception:
                pass
            raise ConnectionError(f"Could not connect to camera, error: {exc}") from exc

        try:
            # Configure camera parameters for Live View, physical shutter button, and single drivemode
            try:
                self._configure_canon_camera(self._camera)
                logger.info("Canon Live View enabled; allowing the camera up to 8s to provide its first preview frame")
            except Exception as target_err:
                logger.debug(f"Note setting camera config: {target_err}")
        except gp.GPhoto2Error as exc:
            logger.debug("error occurred while setting camera config")
            raise ConnectionError(f"Could not connect to camera, error: {exc}") from exc

        # info output
        # camera_config = self._camera.get_config()
        # self.display_config(camera_config.get_children())

    def _configure_canon_camera(self, cam) -> None:
        cfg = cam.get_config()
        # 1. capturetarget -> Memory card (enables physical shutter on camera body)
        try:
            target = cfg.get_child_by_name("capturetarget")
            for i in range(target.count_choices()):
                choice = target.get_choice(i)
                if "card" in choice.lower() or "sd" in choice.lower():
                    target.set_value(choice)
                    logger.info(f"Set camera capturetarget to '{choice}' (Enables physical shutter button)")
                    break
        except Exception:
            pass

        # 2. viewfinder / eosviewfinder -> 1 (turn on Live View stream)
        for vf_name in ("viewfinder", "eosviewfinder"):
            try:
                vf = cfg.get_child_by_name(vf_name)
                vf.set_value(1)
                logger.info(f"Set camera {vf_name} to 1 (Live View active)")
            except Exception:
                pass

        # 3. output -> TFT + PC (stream live feed to PC while keeping camera LCD on)
        try:
            out = cfg.get_child_by_name("output")
            choices = [out.get_choice(i) for i in range(out.count_choices())] if hasattr(out, "count_choices") else []
            for preferred in ("TFT + PC", "At the camera and on the PC", "PC"):
                if preferred in choices:
                    out.set_value(preferred)
                    logger.info(f"Set camera output to '{preferred}'")
                    break
        except Exception as out_err:
            logger.debug(f"Output setting note: {out_err}")

        # 4. drivemode -> Single Shooting (prevent multi-shot bursts on shutter press)
        try:
            dm = cfg.get_child_by_name("drivemode")
            for i in range(dm.count_choices()):
                choice = dm.get_choice(i)
                if "single" in choice.lower() or choice == "0":
                    dm.set_value(choice)
                    logger.info(f"Set camera drivemode to '{choice}' (Single Shooting)")
                    break
        except Exception as dm_err:
            logger.debug(f"Drivemode setting note: {dm_err}")

        # Canon action widgets report UI Lock as value 2 (always
        # changed). Set it explicitly so the physical shutter starts
        # unlocked instead of inheriting an ambiguous driver value.
        try:
            cfg.get_child_by_name("uilock").set_value(0)
            self._desired_ui_locked = False
            self._ui_locked = False
        except Exception:
            self._desired_ui_locked = False
            self._ui_locked = None

        self._liveview_grace_until = time.monotonic() + 8.0
        cam.set_config(cfg)

    def _trigger_physical_shutter_job(self, folder: str, name: str, container) -> bool:
        self._begin_capture_transfer()
        self._pending_physical_capture = (folder, name)
        try:
            container.processing_service.trigger_action("collage", 0)
            return True
        except Exception as exc:
            logger.warning("Failed to trigger collage on physical shutter %s/%s: %s", folder, name, exc)
            self._capture_busy = False
            self._capture_started_at = 0.0
            self._desired_ui_locked = False
            self._pending_physical_capture = None
            return False

    def display_config(self, camera_config):
        for child in camera_config:
            if GpWidgets(child.get_type()) == GpWidgets.GP_WIDGET_SECTION:
                print("\n\n- SECTION: ", end="")
                print(f"{child.get_label()} ({child.get_name()})")
                self.display_config(child)
            else:
                print(f"{child.get_name()}='{child.get_value()}'   {child.get_label()}   {' (readonly) ' if child.get_readonly() else ''}  ")
                print(f"type={GpWidgets(child.get_type()).name}")
                if GpWidgets(child.get_type()) in (GpWidgets.GP_WIDGET_RADIO, GpWidgets.GP_WIDGET_MENU):
                    print(f"available options {{index, value}} are {[{idx: choice} for idx, choice in enumerate(child.get_choices())]}")
                print()

    def _reconnect_camera(self) -> bool:
        logger.warning("Attempting camera USB reset and reconnection...")
        try:
            if self._camera:
                try:
                    self._camera.exit()
                except Exception:
                    pass
        except Exception:
            pass
        self._camera = None
        time.sleep(0.5)
        new_cam = None
        try:
            import usb.core
            canon_devs = [d for d in usb.core.find(find_all=True) if d.idVendor == 0x04a9]
            for dev in canon_devs:
                try:
                    dev.reset()
                except Exception:
                    pass
            time.sleep(1.5)
            new_cam = gp.Camera()
            new_cam.init()
            try:
                self._configure_canon_camera(new_cam)
            except Exception as cfg_err:
                logger.debug(f"Note setting camera config during reconnect: {cfg_err}")
            self._camera = new_cam
            logger.info("✅ Camera successfully reconnected and Live View restored!")
            return True
        except Exception as err:
            if new_cam is not None:
                try:
                    new_cam.exit()
                except Exception:
                    pass
            logger.warning(f"Camera reconnection failed: {err}")
            return False

    def teardown_resource(self):
        if getattr(self, "_ptp_killer_stop", None):
            self._ptp_killer_stop.set()
        if self._camera:
            try:
                self._camera.exit()
            except Exception:
                pass

    def _handle_still_request(self, req: StillRequest) -> None:
        # capture hq picture
        logger.info("taking hq picture")

        # hold a list of captured files during capture. this is needed if JPG+RAW is shot.
        # there is no guarantee that the first is the JPG and second the RAW image. Also depending on the capturetarget
        # the sequence the images appear can be different. gp.GP_CAPTURE_IMAGE vs gp.GP_CAPTURE_RAW seems not reliable to rely on
        captured_files: list[tuple[str, str]] = []

        if self._pending_physical_capture:
            captured_files.append(self._pending_physical_capture)
            self._pending_physical_capture = None
            logger.info(f"📸 Using photo from physical camera shutter: {captured_files}")
        else:
            # Approval/processing has finished. Allow exactly one
            # new press before locking the Canon controls again.
            self._desired_ui_locked = False
            self._apply_canon_ui_lock()
            logger.info("📸 Waiting for camera shutter press while streaming preview...")
            # Drain any stale events from camera buffer first
            self._drain_events(timeout_ms=100)
            start_wait_time = time.time()
            unexpected_preview_failures = 0

            while not self._stop_event.is_set():
                if self._camera is None:
                    raise RuntimeError("Canon camera connection unavailable during shutter wait")
                self._framerate.wait_until_fps(25)
                try:
                    evt_typ_cam, evt_data_cam = self._camera.wait_for_event(10)
                    if evt_typ_cam == gp.GP_EVENT_FILE_ADDED:
                        base_name = os.path.splitext(evt_data_cam.name)[0]
                        elapsed = time.time() - start_wait_time
                        if base_name in self._processed_camera_files:
                            logger.debug(f"Ignoring duplicate camera event: {evt_data_cam.name}")
                        elif elapsed < 1.2:
                            # Burst/ghost shot that happened too fast (< 1.2s after entering state)
                            logger.info(f"Debounce: ignoring ghost burst event {evt_data_cam.name} arriving only {elapsed:.2f}s after previous shot")
                            self._processed_camera_files.add(base_name)
                        else:
                            logger.info(f"📸 PHYSICAL SHUTTER FIRED: {evt_data_cam.folder}/{evt_data_cam.name}")
                            self._begin_capture_transfer()
                            self._processed_camera_files.add(base_name)
                            captured_files.append((evt_data_cam.folder, evt_data_cam.name))
                            break
                except Exception:
                    pass

                try:
                    camera_file = self._camera.capture_preview()
                    self._frame_tick()
                    img_bytes = memoryview(camera_file.get_data_and_size()).tobytes()

                    with self._lores_data[0].condition:
                        self._lores_data[0].data = img_bytes
                        self._lores_data[0].condition.notify_all()
                    unexpected_preview_failures = 0
                except Exception as prev_err:
                    err_str = str(prev_err)
                    if "[-52]" in err_str or "[-4]" in err_str or "No such device" in err_str or "[-10]" in err_str or "[-105]" in err_str:
                        logger.warning(f"Camera USB disconnect during shutter wait ({err_str})")
                        if self._reconnect_camera():
                            unexpected_preview_failures = 0
                            continue
                        raise RuntimeError("Canon USB reconnection failed during shutter wait") from prev_err
                    elif "Camera Not Ready" in err_str or "0xa102" in err_str or "[-1]" in err_str or "[-35]" in err_str:
                        # Shutter may have been pressed, or camera is busy. Check briefly for FILE_ADDED event
                        wait_start = time.time()
                        while not self._stop_event.is_set() and (time.time() - wait_start < 1.5):
                            try:
                                evt_typ_cam, evt_data_cam = self._camera.wait_for_event(100)
                                if evt_typ_cam == gp.GP_EVENT_FILE_ADDED:
                                    base_name = os.path.splitext(evt_data_cam.name)[0]
                                    elapsed = time.time() - start_wait_time
                                    if base_name in self._processed_camera_files:
                                        pass
                                    elif elapsed < 1.2:
                                        logger.info(f"Debounce: ignoring ghost burst event {evt_data_cam.name}")
                                        self._processed_camera_files.add(base_name)
                                    else:
                                        logger.info(f"📸 PHYSICAL SHUTTER FIRED: {evt_data_cam.folder}/{evt_data_cam.name}")
                                        self._begin_capture_transfer()
                                        self._processed_camera_files.add(base_name)
                                        captured_files.append((evt_data_cam.folder, evt_data_cam.name))
                                        break
                            except Exception:
                                time.sleep(0.05)
                        if captured_files:
                            break
                        # No file added; re-activate viewfinder so Live View continues smoothly
                        try:
                            cfg = self._camera.get_config()
                            try: cfg.get_child_by_name("viewfinder").set_value(1)
                            except Exception: pass
                            try: cfg.get_child_by_name("output").set_value("TFT + PC")
                            except Exception: pass
                            self._camera.set_config(cfg)
                        except Exception:
                            pass

                    else:
                        # If preview fails before a frame is produced, the FPS
                        # limiter has no timestamp to wait on. Without backoff
                        # this loop can spin at 100% CPU and starve HTTP requests.
                        unexpected_preview_failures += 1
                        if unexpected_preview_failures == 1 or unexpected_preview_failures % 5 == 0:
                            logger.warning("Canon preview failed while waiting for shutter (%s/10): %s", unexpected_preview_failures, prev_err)
                        if unexpected_preview_failures >= 10:
                            raise RuntimeError("Canon preview repeatedly failed during shutter wait") from prev_err
                        time.sleep(0.2)

        logger.info(f"got {captured_files=}")

        # now decide which file to download, we watch out for the jpg
        file_to_download = None
        for captured_file in captured_files:
            base_name = os.path.splitext(captured_file[1])[0]
            self._processed_camera_files.add(base_name)
            _, file_extension = os.path.splitext(captured_file[1])
            if str(file_extension).lower() in (".jpg", ".jpeg"):
                file_to_download = captured_file
                logger.info(f"determined {file_to_download=}")
                break

        # check if a file was found. if no, maybe capture failed or
        if file_to_download is None:
            self._finish_capture_transfer()
            logger.critical("no capture or no jpeg captured! shooting in raw-only mode?")
            time.sleep(0.6)
            return

        # read from camera with retry
        Path("tmp").mkdir(parents=True, exist_ok=True)
        saved_file = None
        for attempt in range(4):
            filepath: Path | None = None
            try:
                with NamedTemporaryFile(
                    mode="wb",
                    delete=False,
                    dir="tmp",
                    prefix=f"{filename_str_time()}_gphoto2_",
                    suffix=".jpg",
                ) as tmp:
                    filepath = Path(tmp.name)

                camera_file = self._camera.file_get(file_to_download[0], file_to_download[1], gp.GP_FILE_TYPE_NORMAL)  # pyright: ignore [reportAttributeAccessIssue]
                camera_file.save(str(filepath))
                saved_file = filepath
                break
            except Exception as exc:
                if filepath is not None:
                    filepath.unlink(missing_ok=True)
                logger.warning(f"Error reading camera file (attempt {attempt+1}/4): {exc}")
                time.sleep(0.3)

        if saved_file is None:
            self._finish_capture_transfer()
            logger.critical("Failed to download captured image from camera after retries!")
            return

        # Drain leftover events from this capture
        self._drain_events()

        # Re-activate Live View viewfinder on Canon RP after high-res transfer
        try:
            cfg = self._camera.get_config()
            try:
                cfg.get_child_by_name("viewfinder").set_value(1)
            except Exception: pass
            try:
                cfg.get_child_by_name("output").set_value("TFT + PC")
            except Exception: pass
            try:
                cfg.get_child_by_name("uilock").set_value(1)
            except Exception: pass
            self._camera.set_config(cfg)
            self._ui_locked = True
            logger.info("Live View viewfinder re-activated after still capture!")
        except Exception as vf_err:
            logger.debug(f"Viewfinder re-activate note: {vf_err}")

        with req.condition:
            req.result_file = saved_file
            req.condition.notify_all()
        self._finish_capture_transfer()

    def run_service(self):
        assert gp

        preview_failcounter = 0

        while not self._stop_event.is_set():  # repeat until stopped
            if self._drain_requested:
                self._drain_requested = False
                self._drain_events()

            with self._hires_lock:
                req = self._hires_queue.popleft() if self._hires_queue else None

            if req:
                if isinstance(req, StillRequest):
                    try:
                        self._handle_still_request(req)
                    finally:
                        if req.result_file is None:
                            self._capture_busy = False
                            self._capture_started_at = 0.0
                            self._desired_ui_locked = False
                            with req.condition:
                                req.condition.notify_all()
                else:
                    logger.warning(f"this backend does not support {type(req)} requests")
                    continue
            else:
                # lores/preview stream
                self._mode_machine.process_switchmode()

                from ...container import container

                # Keep the camera body unlocked only while waiting for the
                # first press of a new job. During transfer/approval it remains
                # locked, preventing extra files from being captured.
                if not container.processing_service.is_occupied() and not self._capture_busy:
                    self._desired_ui_locked = False
                self._apply_canon_ui_lock()

                self._framerate.wait_until_fps(25)

                # Check if user physically pressed shutter on camera body (only when booth is not occupied)
                try:
                    evt_typ_cam, evt_data_cam = self._camera.wait_for_event(1)
                    if evt_typ_cam == gp.GP_EVENT_FILE_ADDED:
                        base_name = os.path.splitext(evt_data_cam.name)[0]
                        if base_name in self._processed_camera_files:
                            logger.debug(f"Ignoring duplicate/old camera event: {evt_data_cam.name}")
                        elif time.time() - self._last_job_completed_time < 3.0:
                            logger.info(f"Debounce: ignoring shutter event immediately after collage completion: {evt_data_cam.name}")
                            self._processed_camera_files.add(base_name)
                        elif not container or not container.processing_service.is_occupied():
                            logger.info(f"📸 PHYSICAL SHUTTER PRESSED ON CAMERA! File added: {evt_data_cam.folder}/{evt_data_cam.name}")
                            self._processed_camera_files.add(base_name)
                            if self._trigger_physical_shutter_job(evt_data_cam.folder, evt_data_cam.name, container):
                                continue
                        else:
                            self._processed_camera_files.add(base_name)
                            logger.warning(f"Ignored extra shutter event while review lock is active: {evt_data_cam.name}")
                except Exception:
                    pass

                try:
                    camera_file = self._camera.capture_preview()
                    self._frame_tick()
                    img_bytes = memoryview(camera_file.get_data_and_size()).tobytes()

                    with self._lores_data[0].condition:
                        self._lores_data[0].data = img_bytes
                        self._lores_data[0].condition.notify_all()
                    preview_failcounter = 0

                except Exception as exc:
                    err_str = str(exc)
                    if "Camera Not Ready" in err_str or "0xa102" in err_str or "[-1]" in err_str or "[-35]" in err_str:
                        # Shutter may have been pressed, or viewfinder timed out. Check briefly (300ms) for FILE_ADDED
                        file_found = False
                        try:
                            e_type, e_data = self._camera.wait_for_event(300)
                            if e_type == gp.GP_EVENT_FILE_ADDED:
                                b_name = os.path.splitext(e_data.name)[0]
                                if b_name not in self._processed_camera_files:
                                    self._processed_camera_files.add(b_name)
                                    if time.time() - self._last_job_completed_time < 3.0:
                                        logger.info(f"Debounce: ignoring shutter event immediately after collage completion: {e_data.name}")
                                    elif not container.processing_service.is_occupied():
                                        logger.info(f"📸 PHYSICAL SHUTTER PRESSED ON CAMERA! File added: {e_data.folder}/{e_data.name}")
                                        file_found = self._trigger_physical_shutter_job(e_data.folder, e_data.name, container)
                                    else:
                                        logger.warning(f"Ignored extra shutter event while review lock is active: {e_data.name}")
                        except Exception:
                            pass

                        if file_found:
                            preview_failcounter = 0
                            continue

                        preview_failcounter += 1

                        # A freshly enabled Canon Live View commonly answers
                        # with 0xa102 while its sensor/EVF is still starting.
                        # Rewriting config or resetting USB here interrupts
                        # that startup and can make the camera disappear.
                        if time.monotonic() < self._liveview_grace_until:
                            if preview_failcounter == 1:
                                logger.info("Canon Live View is warming up; waiting for the first preview frame")
                            time.sleep(0.20)
                            continue

                        # If Live View later sleeps, re-activate it sparingly.
                        # Repeated set_config calls prevent some Canon bodies
                        # from ever completing their EVF transition.
                        if preview_failcounter % 10 == 0:
                            try:
                                cfg = self._camera.get_config()
                                try:
                                    cfg.get_child_by_name("viewfinder").set_value(1)
                                except Exception:
                                    pass
                                try:
                                    output = cfg.get_child_by_name("output")
                                    choices = [output.get_choice(i) for i in range(output.count_choices())]
                                    for preferred in ("TFT + PC", "At the camera and on the PC", "PC"):
                                        if preferred in choices:
                                            output.set_value(preferred)
                                            break
                                except Exception:
                                    pass
                                try:
                                    cfg.get_child_by_name("uilock").set_value(1 if self._desired_ui_locked else 0)
                                except Exception:
                                    pass
                                self._camera.set_config(cfg)
                                self._ui_locked = self._desired_ui_locked
                                self._liveview_grace_until = time.monotonic() + 4.0
                                logger.info("Re-activated Canon Live View; waiting for EVF to become ready")
                            except Exception as vf_err:
                                logger.debug(f"Viewfinder re-activate failed: {vf_err}")

                        # Only reconnect after a sustained failure. Four rapid
                        # 0xa102 responses are normal during Canon startup.
                        if preview_failcounter >= 50:
                            logger.warning(f"Repeated Camera Not Ready in idle loop ({preview_failcounter}), attempting USB reconnect...")
                            if self._reconnect_camera():
                                preview_failcounter = 0
                            else:
                                raise RuntimeError("Canon camera did not recover after a sustained Live View failure") from exc
                        time.sleep(0.20)
                        continue

                    if "[-110]" in err_str or "I/O in progress" in err_str or "busy" in err_str.lower():
                        # Camera is saving to SD card or waking up sensor, wait briefly and retry
                        time.sleep(0.06)
                        continue

                    if "[-52]" in err_str or "[-4]" in err_str or "No such device" in err_str or "[-10]" in err_str or "[-105]" in err_str:
                        logger.warning(f"Camera USB reconnect/timeout detected in idle loop ({err_str})")
                        if self._reconnect_camera():
                            preview_failcounter = 0
                            continue
                        raise RuntimeError("Canon camera USB connection was lost and reconnection failed") from exc

                    preview_failcounter += 1

                    if preview_failcounter <= 15:
                        logger.warning(f"error capturing frame from DSLR: {exc}")
                        time.sleep(0.3)
                        continue
                    else:
                        logger.critical(f"aborting capturing frame, camera disconnected? retry to connect {exc}")
                        try:
                            self._camera.exit()
                        except Exception:
                            pass

                        # stop device requested by leaving worker loop, so supervisor can restart
                        raise RuntimeError(f"Error communicating with the camera: {exc}") from exc

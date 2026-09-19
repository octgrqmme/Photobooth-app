import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs';
import path from 'path';

const execAsync = promisify(exec);

export class PrinterService {
  /**
   * Get list of available printers on macOS / Linux via CUPS (lpstat)
   */
  static async getPrinters() {
    try {
      const { stdout: defaultOutput } = await execAsync('lpstat -d').catch(() => ({ stdout: '' }));
      const defaultMatch = defaultOutput.match(/(?:system default destination|ปลายทางค่าเริ่มต้นของระบบ):\s*([^\n\r]+)/i);
      const defaultPrinter = defaultMatch ? defaultMatch[1].trim() : null;

      const { stdout: printersOutput } = await execAsync('lpstat -p').catch(() => ({ stdout: '' }));
      
      const printers = [];
      const lines = printersOutput.split('\n');

      for (const line of lines) {
        // Matches: "printer HP_Ink_Tank... is idle" or Thai "เครื่องพิมพ์ HP_Ink_Tank... อยู่นิ่ง"
        const match = line.match(/(?:printer|เครื่องพิมพ์)\s+([^\s]+)\s+(.*)/i);
        if (match) {
          const name = match[1].trim();
          const status = match[2].trim();
          const isEnabled = status.includes('เปิดใช้งาน') || status.includes('idle') || status.includes('ready') || status.includes('อยู่นิ่ง');
          const isDisabled = !isEnabled && (status.includes('disabled') || status.includes('ปิดใช้งาน') || status.includes('offline'));
          printers.push({
            name,
            isDefault: name === defaultPrinter,
            status,
            online: !isDisabled
          });
        }
      }

      // If no printers parsed via -p, check -a (accepting requests)
      if (printers.length === 0) {
        const { stdout: acceptOutput } = await execAsync('lpstat -a').catch(() => ({ stdout: '' }));
        const aLines = acceptOutput.split('\n');
        for (const line of aLines) {
          const m = line.match(/^([^\s]+)\s+accepting requests/i);
          if (m) {
            printers.push({
              name: m[1],
              isDefault: m[1] === defaultPrinter,
              status: 'Accepting jobs',
              online: true
            });
          }
        }
      }

      return {
        defaultPrinter,
        printers: printers.length > 0 ? printers : (defaultPrinter ? [{ name: defaultPrinter, isDefault: true, status: 'Ready', online: true }] : [])
      };
    } catch (error) {
      console.error('Error fetching printers:', error);
      return { defaultPrinter: null, printers: [], error: error.message };
    }
  }

  /**
   * Print an image file to a specified printer
   * @param {string} filePath - Absolute path to the image file (e.g. .jpg or .png)
   * @param {object} options - { printerName, copies, paperSize, fitToPage }
   */
  static async printFile(filePath, options = {}) {
    const {
      printerName,
      copies = 1,
      paperSize = 'iso_a4_210x297mm', // or '4x6' or 'Custom.2x6in'
      fitToPage = true
    } = options;

    if (!fs.existsSync(filePath)) {
      throw new Error(`Print file not found at path: ${filePath}`);
    }

    // Build lp command
    const args = ['lp'];

    if (printerName) {
      args.push(`-d "${printerName}"`);
    }

    if (copies > 1) {
      args.push(`-n ${parseInt(copies, 10)}`);
    }

    if (fitToPage) {
      args.push('-o fit-to-page');
    }

    if (paperSize) {
      args.push(`-o media=${paperSize}`);
    }

    args.push(`"${filePath}"`);

    const command = args.join(' ');
    console.log(`Executing print command: ${command}`);

    try {
      const { stdout, stderr } = await execAsync(command);
      console.log('Print output:', stdout);
      if (stderr) console.warn('Print stderr:', stderr);

      // Extract job id if present e.g. "request id is HP_Ink_Tank_Wireless_410_series-123 (1 file(s))"
      const jobMatch = stdout.match(/(?:request id is|หมายเลขคำขอคือ)\s+([^\s]+)/i);
      const jobId = jobMatch ? jobMatch[1] : null;

      return {
        success: true,
        command,
        jobId,
        output: stdout.trim()
      };
    } catch (error) {
      console.error('Failed to print file:', error);
      throw new Error(`Print command failed: ${error.message}`);
    }
  }

  /**
   * Get current print jobs in queue
   */
  static async getQueue(printerName) {
    try {
      const cmd = printerName ? `lpstat -o "${printerName}"` : 'lpstat -o';
      const { stdout } = await execAsync(cmd).catch(() => ({ stdout: '' }));
      const jobs = stdout.split('\n').filter(Boolean).map(line => {
        const parts = line.split(/\s+/);
        return {
          jobId: parts[0],
          user: parts[1],
          size: parts[2],
          time: parts.slice(3).join(' ')
        };
      });
      return jobs;
    } catch (error) {
      console.error('Error fetching queue:', error);
      return [];
    }
  }

  /**
   * Cancel a print job
   */
  static async cancelJob(jobId) {
    try {
      await execAsync(`cancel ${jobId}`);
      return { success: true };
    } catch (error) {
      throw new Error(`Failed to cancel job: ${error.message}`);
    }
  }
}

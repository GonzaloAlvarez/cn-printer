// cn-printer: scanservjs local config (mounted at /etc/scanservjs/config.local.js).
//
//  * PDF first — the default pipeline is "PDF (JPG | high-quality)" (scanservjs
//    takes pipelines[0] as the default). The upstream OCR pipelines stay: the
//    image ships tesseract 5.5 with eng/spa/deu/fra/… data; OCR_LANG in the
//    environment picks the languages (e.g. eng+spa).
//  * Google Drive — rclone (scanservjs/Dockerfile) with the remote defined in
//    $RCLONE_CONFIG (Kauket printer.cn_printer_rclone_conf, mounted at
//    /etc/rclone). Two entry points: the "→ Google Drive" pipelines upload the
//    finished file automatically (afterAction), and the Files tab offers
//    "Upload to Google Drive" for anything already scanned.
//  * Brother resolutions pinned to the ones its eSCL endpoint handles well.
const options = { paths: ['/usr/lib/scanservjs'] };
const Process = require(require.resolve('./server/classes/process', options));
const fs = require('fs');
const path = require('path');

const GDRIVE_REMOTE_PATH = (process.env.GDRIVE_REMOTE_PATH || 'gdrive:Scans').replace(/\/+$/, '');
const RCLONE_CONFIG = process.env.RCLONE_CONFIG || '/etc/rclone/rclone.conf';
const RCLONE_FLAGS = '--retries 3 --low-level-retries 10 --contimeout 30s --timeout 5m';
const PDF_HQ = 'PDF (JPG | @:pipeline.high-quality)';
const PDF_OCR = '@:pipeline.ocr | PDF (JPG | @:pipeline.high-quality)';
const ACTION_UPLOAD = 'Upload to Google Drive';
const ACTION_UPLOAD_BG = 'Upload to Google Drive (background)';

// POSIX single-quote escaping: Process.spawn runs through /bin/sh.
const shq = s => `'${String(s).replace(/'/g, `'\\''`)}'`;

async function uploadToDrive(fileInfo) {
  if (!fs.existsSync(RCLONE_CONFIG)) {
    throw new Error(`Google Drive is not configured: ${RCLONE_CONFIG} is missing ` +
      '(mint it with scripts/gdrive-auth.sh, then `kauket get printer.cn_printer_rclone_conf` on the host)');
  }
  const dest = `${GDRIVE_REMOTE_PATH}/${fileInfo.name}`;
  await Process.spawn(`rclone --config ${shq(RCLONE_CONFIG)} ${RCLONE_FLAGS} copyto ${shq(fileInfo.fullname)} ${shq(dest)}`);
  console.log(`[gdrive] uploaded ${fileInfo.name} -> ${dest}`);
}

module.exports = {
  afterConfig(config) {
    config.port = 8080;
    config.host = '::';

    // PDF (high quality) becomes pipelines[0] == the UI default.
    const hq = config.pipelines.find(p => p.description === PDF_HQ);
    const ocr = config.pipelines.find(p => p.description === PDF_OCR);
    if (!hq || !ocr) throw new Error('cn-printer config.local.js: upstream pipeline names changed — update PDF_HQ/PDF_OCR');
    config.pipelines = [hq, ...config.pipelines.filter(p => p !== hq)];

    // Same conversions, then an automatic upload. `ocr.commands` is a getter
    // (it reads config.tesseract/ocrLanguage at call time), so forward it.
    config.pipelines.splice(1, 0,
      { extension: 'pdf', description: 'PDF → Google Drive', commands: hq.commands, afterAction: ACTION_UPLOAD_BG },
      { extension: 'pdf', description: 'PDF (OCR) → Google Drive', get commands() { return ocr.commands; }, afterAction: ACTION_UPLOAD_BG }
    );
  },

  afterDevices(devices) {
    const pinned = [150, 300, 600, 1200];
    for (const device of devices) {
      if (!device.id.startsWith('airscan:')) continue;   // the sane-test device keeps its own range
      const res = device.features['--resolution'];
      if (!res) continue;
      const supported = Array.isArray(res.options) ? pinned.filter(r => res.options.includes(r)) : pinned;
      res.options = supported.length ? supported : pinned;
      res.default = 300;
    }
  },

  actions: [
    {
      // Files tab → select → action. Errors propagate so the UI shows them.
      name: ACTION_UPLOAD,
      async execute(fileInfo) { await uploadToDrive(fileInfo); }
    },
    {
      // Used by the "→ Google Drive" pipelines. scan-controller fires afterAction
      // without awaiting it, so a rejection here would be an unhandled promise
      // rejection (fatal for node >= 15): catch, log, and leave a marker file
      // next to the scan so the failure shows up in the Files tab.
      name: ACTION_UPLOAD_BG,
      async execute(fileInfo) {
        try {
          await uploadToDrive(fileInfo);
        } catch (e) {
          const msg = e && e.message ? e.message : String(e);
          try {
            fs.writeFileSync(path.join(fileInfo.path, `${fileInfo.name}.gdrive-FAILED.txt`),
              `Upload of ${fileInfo.name} to ${GDRIVE_REMOTE_PATH} failed at ${new Date().toISOString()}:\n${msg}\n`);
          } catch (_) { /* nothing more we can do */ }
          console.error(`[gdrive] FAILED ${fileInfo.name}: ${msg}`);
        }
      }
    }
  ]
};

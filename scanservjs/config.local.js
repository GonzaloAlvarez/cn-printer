// cn-printer: scanservjs local config (mounted at /etc/scanservjs/config.local.js).
//
//  * Paperless first — the default pipeline is "PDF → Paperless": the usual
//    high-quality JPG PDF, then the file is moved into the inbox the
//    paperless-forwarder container watches (/srv/scans/inbox), which uploads it
//    to Paperless-ngx (cn-paperless on kaiser) and files it under processed/.
//    Paperless does the OCR, so the plain PDF is what we send. The Files tab
//    offers "Send to Paperless" for anything already scanned.
//  * The upstream PDF/OCR pipelines stay (tesseract 5.5, OCR_LANG picks the
//    languages, e.g. eng+spa) for scans that should NOT go to Paperless.
//  * Google Drive (legacy, demoted to the end of the list) — rclone with the
//    remote in $RCLONE_CONFIG (Kauket printer.cn_printer_rclone_conf). Kept
//    until the Drive migration is finished, then removed.
//  * Brother resolutions pinned to the ones its eSCL endpoint handles well.
const options = { paths: ['/usr/lib/scanservjs'] };
const Process = require(require.resolve('./server/classes/process', options));
const fs = require('fs');
const path = require('path');

const PAPERLESS_INBOX = (process.env.PAPERLESS_INBOX_DIR || '/var/lib/scanservjs/output/inbox').replace(/\/+$/, '');
const GDRIVE_REMOTE_PATH = (process.env.GDRIVE_REMOTE_PATH || 'gdrive:Scans').replace(/\/+$/, '');
const RCLONE_CONFIG = process.env.RCLONE_CONFIG || '/etc/rclone/rclone.conf';
const RCLONE_FLAGS = '--retries 3 --low-level-retries 10 --contimeout 30s --timeout 5m';
const PDF_HQ = 'PDF (JPG | @:pipeline.high-quality)';
const PDF_OCR = '@:pipeline.ocr | PDF (JPG | @:pipeline.high-quality)';
const ACTION_PAPERLESS = 'Send to Paperless';
const ACTION_PAPERLESS_BG = 'Send to Paperless (background)';
const ACTION_UPLOAD = 'Upload to Google Drive';
const ACTION_UPLOAD_BG = 'Upload to Google Drive (background)';

// POSIX single-quote escaping: Process.spawn runs through /bin/sh.
const shq = s => `'${String(s).replace(/'/g, `'\\''`)}'`;

// Same filesystem (inbox is a subdirectory of the output tree), so the rename
// is atomic and the file keeps its owner (uid 1000). The forwarder waits for
// the size to be stable before uploading, so a half-written file is never sent.
function sendToPaperless(fileInfo) {
  if (!fs.existsSync(PAPERLESS_INBOX)) {
    throw new Error(`Paperless inbox ${PAPERLESS_INBOX} is missing (amun printer creates /srv/scans/inbox)`);
  }
  const dest = path.join(PAPERLESS_INBOX, fileInfo.name);
  fs.renameSync(fileInfo.fullname, dest);
  console.log(`[paperless] queued ${fileInfo.name} -> ${dest}`);
}

async function uploadToDrive(fileInfo) {
  if (!fs.existsSync(RCLONE_CONFIG)) {
    throw new Error(`Google Drive is not configured: ${RCLONE_CONFIG} is missing ` +
      '(mint it with scripts/gdrive-auth.sh, then `kauket get printer.cn_printer_rclone_conf` on the host)');
  }
  const dest = `${GDRIVE_REMOTE_PATH}/${fileInfo.name}`;
  await Process.spawn(`rclone --config ${shq(RCLONE_CONFIG)} ${RCLONE_FLAGS} copyto ${shq(fileInfo.fullname)} ${shq(dest)}`);
  console.log(`[gdrive] uploaded ${fileInfo.name} -> ${dest}`);
}

// scan-controller fires a pipeline's afterAction without awaiting it: a
// rejection would be an unhandled promise rejection (fatal for node >= 15).
// Background actions therefore catch, log, and leave a marker file next to
// the scan so the failure shows up in the Files tab; the Files-tab actions
// throw so the UI can show the error.
function backgroundAction(name, tag, fn) {
  return {
    name,
    async execute(fileInfo) {
      try {
        await fn(fileInfo);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        try {
          fs.writeFileSync(path.join(fileInfo.path, `${fileInfo.name}.${tag}-FAILED.txt`),
            `${tag} action on ${fileInfo.name} failed at ${new Date().toISOString()}:\n${msg}\n`);
        } catch (_) { /* nothing more we can do */ }
        console.error(`[${tag}] FAILED ${fileInfo.name}: ${msg}`);
      }
    }
  };
}

module.exports = {
  afterConfig(config) {
    config.port = 8080;
    config.host = '::';

    const hq = config.pipelines.find(p => p.description === PDF_HQ);
    const ocr = config.pipelines.find(p => p.description === PDF_OCR);
    if (!hq || !ocr) throw new Error('cn-printer config.local.js: upstream pipeline names changed — update PDF_HQ/PDF_OCR');

    // pipelines[0] is the UI default. Order: Paperless, plain PDF, the rest of
    // upstream's list, and the legacy Google Drive pair at the end.
    const rest = config.pipelines.filter(p => p !== hq);
    config.pipelines = [
      { extension: 'pdf', description: 'PDF → Paperless', commands: hq.commands, afterAction: ACTION_PAPERLESS_BG },
      hq,
      ...rest,
      { extension: 'pdf', description: 'PDF → Google Drive', commands: hq.commands, afterAction: ACTION_UPLOAD_BG },
      // `ocr.commands` is a getter (reads config.tesseract/ocrLanguage at call time): forward it.
      { extension: 'pdf', description: 'PDF (OCR) → Google Drive', get commands() { return ocr.commands; }, afterAction: ACTION_UPLOAD_BG }
    ];
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
    // Files tab → select → action. Errors propagate so the UI shows them.
    { name: ACTION_PAPERLESS, async execute(fileInfo) { sendToPaperless(fileInfo); } },
    { name: ACTION_UPLOAD, async execute(fileInfo) { await uploadToDrive(fileInfo); } },
    // Used by the pipelines' afterAction.
    backgroundAction(ACTION_PAPERLESS_BG, 'paperless', async fi => sendToPaperless(fi)),
    backgroundAction(ACTION_UPLOAD_BG, 'gdrive', uploadToDrive)
  ]
};

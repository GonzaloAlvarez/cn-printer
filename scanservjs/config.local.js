// cn-printer: scanservjs local config (mounted at /etc/scanservjs/config.local.js).
// Pins the resolution choices for the Brother, drops the OCR pipelines
// (no tesseract in the image we run), and keeps the server defaults otherwise.
module.exports = {
  afterConfig(config) {
    config.port = 8080;
    config.host = '::';
    config.pipelines = config.pipelines.filter(p => !p.description.includes('@:pipeline.ocr'));
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

  afterScan(fileInfo) {}
};

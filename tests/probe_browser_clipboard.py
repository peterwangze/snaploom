"""Compare trusted Chromium paste events backed by the native Windows clipboard."""

import json
from pathlib import Path
import subprocess
import sys
import time


FIXTURE = [35, 88, 159, 255]
PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Native clipboard paste test</title>
<style>
  body { max-width: 28rem; margin: 3rem auto; font: 1rem system-ui; }
  [role=textbox] { min-height: 8rem; padding: 0.75rem; border: 2px solid #34558b; }
  img { width: 80px; image-rendering: pixelated; }
</style>
<h1>Rich chat input</h1>
<p>Paste a synthetic screenshot</p>
<div role="textbox" aria-label="Type a message" contenteditable="true"></div>
<script>
  const input = document.querySelector('[role=textbox]');
  input.addEventListener('paste', async event => {
    event.preventDefault();
    const clipboard = event.clipboardData;
    const files = [...clipboard.files];
    const result = {
      trusted: event.isTrusted,
      types: [...clipboard.types],
      items: [...clipboard.items].map(item => ({kind: item.kind, type: item.type})),
      files: files.map(file => ({name: file.name, type: file.type, size: file.size})),
      plain: clipboard.getData('text/plain'),
      uri: clipboard.getData('text/uri-list'),
      html: clipboard.getData('text/html'),
      decoded: [],
    };
    input.replaceChildren();
    for (const file of files) {
      if (!file.type.startsWith('image/')) continue;
      try {
        const bitmap = await createImageBitmap(file);
        const canvas = document.createElement('canvas');
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        const context = canvas.getContext('2d');
        context.drawImage(bitmap, 0, 0);
        result.decoded.push({
          width: bitmap.width, height: bitmap.height,
          rgba: [...context.getImageData(3, 4, 1, 1).data],
        });
        const image = document.createElement('img');
        image.src = URL.createObjectURL(file);
        image.alt = 'Pasted screenshot';
        input.append(image);
        await image.decode();
      } catch (error) {
        result.decodeError = String(error);
      }
    }
    if (!input.querySelector('img')) input.textContent = result.plain || result.uri;
    result.renderedImage = input.querySelector('img')?.naturalWidth > 0;
    result.renderedText = input.textContent;
    window.pasteResult = result;
  });
</script>
</html>
"""


def publish(repository, scratch):
    sys.path.insert(0, str(repository))
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication
    from snaploom import VERSION
    from snaploom.app import Application

    qt = QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    app = Application(qt, scratch, testing=True)
    app.workspace.set("save_folder", str(scratch / "exports"))
    app.workspace.set("save_pattern", "Synthetic screenshot")
    image = QImage(20, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    image.setPixelColor(3, 4, QColor(*FIXTURE))
    app.output(image, "save-copy", silent=True, record=False)
    assert app.last_saved_file.is_file(), "Save and Copy did not produce its local file"
    ready = {"version": VERSION, "qt_formats": qt.clipboard().mimeData().formats()}
    (scratch / "ready.json").write_text(json.dumps(ready), encoding="utf-8")
    stop = QTimer(interval=25)
    stop.timeout.connect(lambda: qt.quit() if (scratch / "stop").exists() else None)
    stop.start()
    qt.exec()
    app.shutdown()


def observe(browser, repo, destination, label):
    scratch = destination / label
    scratch.mkdir()
    process = subprocess.Popen([sys.executable, __file__, "--publish", str(repo), str(scratch)])
    try:
        ready_path = scratch / "ready.json"
        stop_time = time.monotonic() + 15
        while not ready_path.is_file():
            if process.poll() is not None or time.monotonic() > stop_time:
                raise RuntimeError(f"Unable to seed Windows clipboard for {label}: {process.poll()}")
            time.sleep(0.05)
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        page = browser.new_page()
        try:
            page.set_content(PAGE)
            page.get_by_role("textbox").click()
            page.keyboard.press("Control+v")
            page.wait_for_function("window.pasteResult", timeout=5000)
            result = ready | page.evaluate("window.pasteResult")
            page.screenshot(path=destination / f"{label}.png")
            print(f"{label}: {json.dumps(result)}", flush=True)
            return result
        finally:
            page.close()
    finally:
        (scratch / "stop").touch()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


def compare(previous, candidate, destination):
    from playwright.sync_api import Error, sync_playwright

    destination.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
        except Error:
            browser = playwright.chromium.launch(channel="msedge", headless=False)
        print(f"Headful Chromium {browser.version}", flush=True)
        try:
            prior = observe(browser, previous, destination, "v1.0.22")
            next_ = observe(browser, candidate, destination, "candidate")
        finally:
            browser.close()
    report = {"previous": prior, "candidate": next_}
    (destination / "paste-events.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert prior["trusted"] and next_["trusted"], report
    assert next_["renderedImage"] and not next_["plain"] and not next_["uri"] and not next_["renderedText"], next_
    assert next_["decoded"] == [{"width": 20, "height": 16, "rgba": FIXTURE}], next_


if __name__ == "__main__":
    if sys.argv[1] == "--publish":
        publish(*(Path(value).resolve() for value in sys.argv[2:]))
    else:
        compare(*(Path(value).resolve() for value in sys.argv[1:]))

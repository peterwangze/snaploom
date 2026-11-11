# Third-party software bundled with Snaploom

Snaploom uses the unmodified Community Edition of Qt and Qt for Python (PySide6 and Shiboken6), copyright The Qt Company and the contributors to those projects. Qt and PySide6 are available under the GNU Lesser General Public License version 3 and other licenses stated in their source trees. The LGPLv3 and GPLv3 texts accompany this notice.

- [Qt 6.10.3 corresponding source archive and individual modules](https://download.qt.io/official_releases/qt/6.10/6.10.3/)
- [PySide6 and Shiboken6 6.10.3 corresponding source](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.10.3)
- [Qt for Python build instructions](https://doc.qt.io/qtforpython-6.10/building_from_source/index.html)
- [Qt 6.10 third-party code and notices](https://doc.qt.io/qt-6.10/licenses-used-in-qt.html)
- [RapidOCR source and OCR model notices](https://github.com/RapidAI/RapidOCR)
- [CPython source and license](https://www.python.org/downloads/source/)

To use interface-compatible modified Qt or PySide6 libraries, extract the portable package (or copy the macOS app bundle), close Snaploom, and replace the corresponding shared libraries and plugins in `_internal/PySide6` or `Snaploom.app/Contents/Frameworks/PySide6`. Use replacements built for the same architecture and compatible Qt ABI. On macOS, apply a local ad-hoc signature to the modified copy using `codesign --force --deep --sign - Snaploom.app`. Snaploom itself applies no library integrity checks that prevent this replacement.

Package-specific notices supplied by the bundled distributions are included in this directory. Their terms apply to the respective components.

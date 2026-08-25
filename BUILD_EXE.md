# Building Al-Qemma as a Windows .exe

This turns the website into something a shop employee can run by double-
clicking, with no "open a terminal and type python run.py" step. It still
runs as a local website under the hood (open in the browser, same as
before) - the .exe just starts that server for you and opens the browser
automatically.

**Important:** this has to be built ON Windows. PyInstaller bundles the
actual Python interpreter and native libraries for whatever operating
system you run it on - building on Linux produces a Linux program, not a
Windows .exe. If you're reading this from somewhere without a Windows PC,
the easiest path is a Windows virtual machine, or just running these steps
directly on the shop's own Windows computer.

## 1. One-time setup (on the Windows machine that will build it)

1. Install **Python 3.10+** from [python.org](https://python.org) if it
   isn't already there. During install, tick **"Add python.exe to PATH"**.
2. Open Command Prompt in the `al-qemma` folder (type `cmd` in the folder's
   address bar in File Explorer, or `cd` into it manually).
3. Install everything needed:
   ```
   pip install -r requirements.txt
   ```
4. Install Playwright's browser (needed for PDF export):
   ```
   playwright install chromium
   ```

## 2. Build the .exe

Run the build script:
```
build_exe.bat
```

This runs PyInstaller once for the main app and collects everything into
`dist\AlQemma\`.

If you'd rather run the steps yourself instead of the .bat file:
```
pyinstaller alqemma.spec
mkdir dist\AlQemma
copy dist\AlQemma.exe dist\AlQemma\
```

## 3. What you get

```
dist\AlQemma\
└── AlQemma.exe                          <- double-click this to run it
```

Copy this whole `AlQemma` folder anywhere with write permission - the
Desktop, a folder on `C:\`, a USB stick to move it to another shop
computer. The first time `AlQemma.exe` runs in a folder, it creates an
`instance\` folder right next to itself (the database, product photos,
logs) - moving the `AlQemma` folder later moves the data with it; copying
just the .exe by itself does not.

## 4. Running it day to day

Double-click `AlQemma.exe`. A console window opens (keep it open - closing
it stops the program, same as closing the `python run.py` terminal would),
and your browser opens to the dashboard automatically. To stop, close that
console window.

## 5. Limitations worth knowing about

- **Antivirus / SmartScreen warnings are normal and expected.** PyInstaller
  exes are unsigned, and Windows (and most antivirus software) is
  suspicious of unsigned, unfamiliar .exe files by default - this is true
  of basically every small in-house tool built this way, not a sign
  anything is actually wrong. Click "More info" → "Run anyway" on the
  SmartScreen prompt. If you want to get rid of this permanently, the
  proper fix is buying a code-signing certificate and signing the exe,
  which costs money and isn't something this guide covers.
- **The .exe is large** (several hundred MB) because it bundles a full
  Python runtime and all the libraries (Pillow, openpyxl, reportlab,
  Playwright). That's normal for PyInstaller and not a bug.
- **Playwright's Chromium browser is NOT bundled into the .exe.** It's a
  separate ~300MB download that lives in a cache folder on whichever
  Windows account ran `playwright install chromium`. If you move
  `AlQemma.exe` to a different computer, run `playwright install chromium`
  on that computer too before generating PDF receipts. This is a deliberate
  tradeoff: bundling a real browser inside the exe is possible but
  meaningfully more complex to set up correctly, and most shops will only
  need to do this once per computer anyway.
- **Rebuild after every code change.** The .exe is a snapshot - editing
  the Python/template files afterward does nothing until you run
  `build_exe.bat` again.

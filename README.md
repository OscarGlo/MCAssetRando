# Minecraft Asset Randomizer 1.1

## Running

### Windows

Download and run the `.exe` from the releases tab to the right of this page.

### Cross-platform

```commandline
python -m pip install -r requirements.txt
python main.py
```

## Building

This will generate an executable for **your current platform only**.

### Windows

```commandline
python -m pip install pyinstaller
python -m PyInstaller main.py --onefile --noconsole -n MCAssetRando --icon resources\icon.png --add-data="resources\icon.png;resources"
```

### UNIX

```commandline
python -m pip install pyinstaller
python -m PyInstaller main.py --onefile --noconsole -n MCAssetRando --icon resources/icon.png --add-data="resources/icon.png:resources"
```
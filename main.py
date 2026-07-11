import json
import os
import re
import shutil
import sys
import time
import zipfile
from zipfile import ZipFile

import pandas as pd
import requests
from PySide6.QtGui import QIcon
import PySide6.QtCore as qc
import PySide6.QtWidgets as qw

from const import DEFAULT_TEXTURE_TYPES, TEXTURE_TYPES, DEFAULT_SOUND_TYPES, SOUND_TYPES, MODEL_TYPES, TEXT_TYPES, \
    DEFAULT_TEXT_TYPES, DEFAULT_MODEL_TYPES
from include_list import IncludeList
from util import transparency_amount, transfer_palette
from versions import get_format, VERSIONS


APP_VERSION = "1.0"

ROOT = "assets/minecraft/"
RE_TEXTURE = rf"{ROOT}textures/([a-z]+)/.*?\.png$"
RE_MODEL = rf"{ROOT}models/([a-z]+)/.*?\.json$"
RE_TEXT = rf"{ROOT}(?:lang/(en_us)\.json|texts/(splashes)\.txt)$"
RE_SOUND = rf"{ROOT}sounds/([a-z]+)/.*?\.ogg$"

BLOCK_MODEL_BLACKLIST = [
    "block",
    "carpet",
    "coral",
    "crop",
    "cross",
    "cube",
    "flower_pot_cross",
    "inner_stairs",
    "outer_stairs",
    "pressure_plate",
    "stairs",
    "template",
    "tinted_cross",
    "wall",
]
RE_MODEL_BLACKLIST = rf"(item/generated|block/({'|'.join(BLOCK_MODEL_BLACKLIST)}))(_.*)?\.json$"

MAX_SEED = 2 ** 31 - 1

BAR_COUNT = 1000
STEP_COUNT = 9
STEP_SIZE = BAR_COUNT / STEP_COUNT

def get_seed() -> int:
    return int(time.time() * 1000) % MAX_SEED


class Step:
    def __init__(self, desc: str, subtotal: int = 0, start: bool = False):
        self.desc = desc
        self.subtotal = subtotal
        self.start = start


# noinspection PyAttributeOutsideInit
class Window(qw.QWidget):
    def __init__(self):
        super().__init__()

        self.generate_worker = None
        self.running = False

        self.layout = qw.QVBoxLayout(self)

        self.setWindowIcon(QIcon("./resources/icon.png"))
        self.setWindowTitle(f"Minecraft Asset Randomizer {APP_VERSION}")

        # Randomizer options
        options_scroll = qw.QScrollArea()
        self.options = qw.QWidget()
        options_layout = qw.QVBoxLayout(self.options)
        options_layout.setSpacing(10)
        options_scroll.setWidget(self.options)
        options_scroll.setWidgetResizable(True)
        self.layout.addWidget(options_scroll)

        self.global_options(options_layout)
        self.texture_options(options_layout)
        self.sound_options(options_layout)
        self.text_options(options_layout)
        self.model_options(options_layout)

        # Controls
        self.layout.addSpacing(20)

        self.generate = qw.QPushButton("Generate resource pack")
        self.generate.setStyleSheet("font-size: 16px; padding: 8px")
        self.generate.clicked.connect(self.on_generate_clicked)
        self.layout.addWidget(self.generate)

        self.progress = qw.QWidget()
        self.progress.setVisible(False)
        progress_layout = qw.QVBoxLayout(self.progress)
        self.layout.addWidget(self.progress)

        self.progress_value = 0
        self.progress_subvalue = 0
        self.progress_subtotal = 0
        self.progress_desc = ""

        self.progress_bar = qw.QProgressBar(minimum=0, maximum=BAR_COUNT, value=self.progress_value)
        progress_layout.addWidget(self.progress_bar)

        self.progress_text = qw.QLabel("", alignment=qc.Qt.AlignmentFlag.AlignCenter)
        progress_layout.addWidget(self.progress_text)

        # Footer
        self.footer()


    def update_progress(self):
        if self.progress_subtotal == 0:
            self.progress_text.setText(self.progress_desc)
            self.progress_bar.setValue(round(self.progress_value))
        else:
            self.progress_text.setText(
                f"{self.progress_desc} ({self.progress_subvalue}/{self.progress_subtotal})"
            )
            self.progress_bar.setValue(
                round(self.progress_value + STEP_SIZE * (self.progress_subvalue / self.progress_subtotal))
            )


    def progress_step(self, description, subtotal=0, start=False):
        self.progress_value = 0 if start else (self.progress_value + STEP_SIZE)
        self.progress_subtotal = subtotal
        self.progress_desc = description
        self.update_progress()


    def progress_substep(self, subvalue):
        self.progress_subvalue = subvalue
        self.update_progress()


    def global_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Global")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.version = qw.QComboBox()
        self.version.addItems([".".join([str(n) for n in v]) for v in VERSIONS])
        layout.addRow(qw.QLabel("Version"), self.version)

        self.rand_seed = qw.QCheckBox("Randomize seed")
        self.rand_seed.setChecked(True)
        self.rand_seed.stateChanged.connect(self.on_rand_seed_checked)
        layout.addRow(self.rand_seed)

        self.seed = qw.QSpinBox(minimum=0, maximum=MAX_SEED, value=get_seed())
        self.seed.setDisabled(True)
        layout.addRow(qw.QLabel("Seed"), self.seed)


    def on_rand_seed_checked(self, checked):
        self.seed.setDisabled(checked)


    def texture_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Textures")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.texture_types = [*DEFAULT_TEXTURE_TYPES]

        type_select = IncludeList([*TEXTURE_TYPES.keys()], self.texture_types)
        layout.addRow(type_select)

        self.match_transparency = qw.QCheckBox("Match transparency")
        self.match_transparency.setChecked(True)
        self.match_transparency.stateChanged.connect(self.match_transparency_changed)
        layout.addRow(self.match_transparency)

        self.transparency_bins = qw.QSpinBox(minimum=2, maximum=16, value=8)
        layout.addRow(qw.QLabel("Transparency bins"), self.transparency_bins)

        self.keep_palette = qw.QCheckBox("Keep color palette")
        self.keep_palette.stateChanged.connect(self.keep_palette_changed)
        layout.addRow(self.keep_palette)

        self.palette_size = qw.QSpinBox(minimum=2, maximum=64, value=16)
        self.palette_size.setDisabled(True)
        layout.addRow(qw.QLabel("Palette size"), self.palette_size)


    def match_transparency_changed(self, checked):
        self.transparency_bins.setEnabled(checked)


    def keep_palette_changed(self, checked):
        self.palette_size.setEnabled(checked)


    def sound_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Sounds")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.sound_types = [*DEFAULT_SOUND_TYPES]

        type_select = IncludeList([*SOUND_TYPES.keys()], self.sound_types)
        layout.addRow(type_select)


    def text_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Text")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.text_types = [*DEFAULT_TEXT_TYPES]

        type_select = IncludeList([*TEXT_TYPES.keys()], self.text_types)
        layout.addRow(type_select)


    def model_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Models")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.model_types = [*DEFAULT_MODEL_TYPES]

        type_select = IncludeList([*MODEL_TYPES.keys()], self.model_types)
        layout.addRow(type_select)


    def footer(self):
        box = qw.QWidget()
        box.setFixedHeight(35)
        layout = qw.QHBoxLayout(box)
        self.layout.addWidget(box)

        layout.addWidget(
            qw.QLabel("<font color='gray'>By OscarGlo</font>")
        )
        layout.addWidget(
            qw.QLabel(
                f"<font color='gray'>Version {APP_VERSION}</font>",
                alignment=qc.Qt.AlignmentFlag.AlignTrailing
            )
        )


    def on_generate_clicked(self):
        if self.running:
            self.generate_worker.stop()
        else:
            self.generate_worker = GenerateWorker(self)
            self.generate_worker.start()

            self.generate_worker.running.connect(self.on_running)
            self.generate_worker.seed.connect(self.on_seed)
            self.generate_worker.step.connect(self.on_step)
            self.generate_worker.substep.connect(self.on_substep)


    @qc.Slot()
    def on_running(self, running: bool):
        self.running = running
        self.progress.setVisible(running)
        self.options.setDisabled(running)
        self.generate.setText("Cancel generation" if running else "Generate resource pack")

    @qc.Slot()
    def on_seed(self, seed: int):
        self.seed.setValue(seed)

    @qc.Slot()
    def on_step(self, step: Step):
        self.progress_step(step.desc, step.subtotal, step.start)

    @qc.Slot()
    def on_substep(self, subvalue: int):
        self.progress_substep(subvalue)


class GenerateWorker(qc.QThread):
    running = qc.Signal(bool)
    seed = qc.Signal(int)
    step = qc.Signal(Step)
    substep = qc.Signal(int)

    def __init__(self, win: Window):
        super().__init__()

        self.win = win
        self.pack_name = None
        self.root = os.getcwd()
        self.stopped = False


    def clean(self, pack=False):
        os.chdir(self.root)

        if os.path.exists("assets"):
            shutil.rmtree("assets")
        if os.path.exists("assets.zip"):
            os.remove("assets.zip")
        if pack and self.pack_name is not None and os.path.exists(self.pack_name):
            os.remove(self.pack_name)


    def stop(self):
        self.stopped = True
        self.running.emit(False)

    
    def run(self):
        self.running.emit(True)

        version_str = self.win.version.currentText()
        version = [int(n) for n in version_str.split(".")]
        if self.win.rand_seed.isChecked():
            seed = get_seed()
            self.seed.emit(seed)
        else:
            seed = self.win.seed.value()
        self.pack_name = f"pack_{version_str}_{seed}.zip"

        texture_types = [t for label in self.win.texture_types for t in TEXTURE_TYPES[label]]
        sound_types = [t for label in self.win.sound_types for t in SOUND_TYPES[label]]
        text_types = [t for label in self.win.text_types for t in TEXT_TYPES[label]]
        model_types = [t for label in self.win.model_types for t in MODEL_TYPES[label]]

        # Cleanup
        self.step.emit(Step("Preparing download...", start=True))
        self.clean(True)

        # Download asset pack
        self.step.emit(Step(f"Downloading assets for version {version_str}..."))
        url = f"https://api.github.com/repos/InventivetalentDev/minecraft-assets/zipball/tags/{version_str}"
        with requests.get(url) as r, open("assets.zip", "wb") as f:
            if self.stopped:
                self.clean(True)
                return
            f.write(r.content)

        # Extract assets
        assets = pd.DataFrame({
            "path": pd.Series(dtype=str),
            "type": pd.Series(dtype=str),
            "subtype": pd.Series(dtype=str),
        })

        def add_asset(
            path: str,
            asset_type: str,
            re_include: str,
            subtypes: list[str] | bool = True,
            keep_subtypes: bool | list[str] | None = None,
            re_exclude: str | None = None,
        ) -> bool:
            nonlocal assets

            match = re.search(re_include, path)
            if match and (re_exclude is None or not re.search(re_exclude, path)):
                subtype = match.group(1)
                if subtypes == True or (isinstance(subtypes, list) and subtype in subtypes):
                    if keep_subtypes == True or (isinstance(keep_subtypes, list) and subtype in keep_subtypes):
                        asset = (path, asset_type, subtype)
                    else:
                        asset = (path, asset_type, None)

                    assets = pd.concat([
                        assets,
                        pd.DataFrame([asset], columns=assets.columns)
                    ], ignore_index=True)
                    return True

            return False

        with ZipFile("assets.zip", "r") as archive:
            if not os.path.exists("assets"):
                os.mkdir("assets")
            os.chdir("assets")

            self.step.emit(Step("Extracting assets...", len(archive.filelist)))
            for i, f in enumerate(archive.filelist):
                if self.stopped:
                    archive.close()
                    self.clean(True)
                    return

                self.substep.emit(i)

                name = f.filename
                path = name[name.index("/") + 1:]

                if (
                    add_asset(path, "texture", RE_TEXTURE, texture_types, False) or
                    add_asset(path, "sound", RE_SOUND, sound_types, False) or
                    add_asset(path, "text", RE_TEXT, text_types, False) or
                    add_asset(path, "model", RE_MODEL, model_types, False, RE_MODEL_BLACKLIST)
                ):
                    archive.extract(f)
                    target = os.path.dirname(path)
                    os.makedirs(target, exist_ok=True)
                    shutil.move(name, target)

        # Add extra metadata
        if self.win.match_transparency.isChecked():
            transparency_bins = self.win.transparency_bins.value()

            files = list(df for _, df in assets.iterrows())
            transparency = []
            self.step.emit(Step(f"Calculating transparency...", len(files)))
            for i, asset in enumerate(files):
                if self.stopped:
                    self.clean(True)
                    return

                self.substep.emit(i)

                transparency.append(
                    int(transparency_amount(asset["path"]) * transparency_bins) if asset["type"] == "texture" else None
                )
            assets["transparency"] = transparency

        # Shuffle strings
        if text_types:
            self.step.emit(Step("Shuffling text..."))

            translations = pd.DataFrame({
                "path": pd.Series(dtype=str),
                "key": pd.Series(dtype=str),
                "value": pd.Series(dtype=str),
                "placeholders": pd.Series(dtype=int),
            })
            for _, lang in assets[assets["path"].str.contains("lang")].iterrows():
                path = lang["path"]
                with open(path, encoding="utf-8") as f:
                    lang_ts = json.load(f)

                df = pd.DataFrame({
                    "key": lang_ts.keys(),
                    "value": lang_ts.values(),
                })
                df["path"] = path
                df["placeholders"] = df["value"].map(
                    lambda v: len([m for m in re.findall(r"%(?:%|s|[0-9]+)", v) if m != "%%"])
                )
                translations = pd.concat([translations, df], ignore_index=True)

            if not translations.empty:
                shuffled_ts = pd.concat(
                    group.sample(frac=1, random_state=seed).reset_index().set_index(group.index)
                    for _, group in translations.groupby("placeholders")
                )
                translations["new_value"] = shuffled_ts["value"]

                for path, content in translations.groupby("path"):
                    with open(path, "w") as f:
                        json.dump({ts["key"]: ts["new_value"] for _, ts in content.iterrows()}, f, indent=2)

        # Shuffle assets
        self.step.emit(Step("Shuffling assets..."))
        criterion = ["type", "subtype"]
        if self.win.match_transparency.isChecked():
            criterion.append("transparency")

        if not assets.empty:
            shuffled = pd.concat(
                group.sample(frac=1, random_state=seed).reset_index().set_index(group.index)
                for _, group in assets.groupby(criterion, dropna=False)
            )
            props = ["path"]
            new_props = ["new_path"]

            shuffled[new_props] = shuffled[props]

            assets = assets.join(shuffled[new_props])

        # Transfer palettes
        if self.win.keep_palette.isChecked():
            palette_size = self.win.palette_size.value()
            textures = list(assets[assets["type"] == "texture"].iterrows())
            self.step.emit(Step(f"Transferring palettes...", len(textures)))
            for i, (_, asset) in enumerate(textures):
                if self.stopped:
                    self.clean(True)
                    return

                self.substep.emit(i)
                with transfer_palette(asset["new_path"], asset["path"], palette_size) as img:
                    img.save(asset["path"])

        # Rename files
        with zipfile.ZipFile(os.path.join("..", self.pack_name), "w") as pack:
            for (atype, subtype), group in assets.groupby(["type", "subtype"], dropna=False):
                group_assets = list(group.iterrows())
                self.step.emit(
                    Step(
                        f"Writing {"" if pd.isna(subtype) else f"{subtype} "}{atype} assets...",
                        len(group_assets)
                    )
                )
                for i, (_, asset) in enumerate(group_assets):
                    if self.stopped:
                        pack.close()
                        self.clean(True)
                        return

                    self.substep.emit(i)
                    pack.write(asset["path"], asset["new_path"])

            fmt = get_format(version)
            meta = {
                "pack": {
                    "pack_format": fmt,
                    "min_format": [fmt, 0],
                    "max_format": [fmt, 0],
                    "description": [
                        "Randomly shuffled assets!\n",
                        {"text": f"Seed: {seed}", "color": "gray", "italic": True}
                    ]
                }
            }
            pack.writestr("pack.mcmeta", json.dumps(meta))

        # Cleanup
        self.step.emit(Step("Cleaning up downloaded files..."))
        self.clean()

        self.running.emit(False)


if __name__ == "__main__":
    app = qw.QApplication([])

    widget = Window()
    widget.show()
    widget.resize(600, 800)

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        sys.exit(0)
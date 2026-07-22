import json
import math
import os
import random
import re
import shutil
import sys
import time
import zipfile
from zipfile import ZipFile
import colorsys

import pandas as pd
import requests
from PIL import Image
from PySide6.QtGui import QIcon
import PySide6.QtCore as qc
import PySide6.QtWidgets as qw

from src.const import DEFAULT_TEXTURE_TYPES, TEXTURE_TYPES, SOUND_TYPES, MODEL_TYPES, TEXT_TYPES, \
    DEFAULT_LOCKED_SOUND_TYPES, LOOT_TABLES_TYPES, RECIPE_TYPES
from src.include_list import IncludeList
from src.util import transparency_amount, transfer_palette, colorize
from src.versions import get_format, VERSIONS


APP_VERSION = "1.3"

ROOT_ASSETS = "assets/minecraft/"
RE_TEXTURE = rf"{ROOT_ASSETS}textures/(\w+)/.*?\.png$"
RE_MODEL = rf"{ROOT_ASSETS}models/(\w+)/.*?\.json$"
RE_TEXT = rf"{ROOT_ASSETS}(?:lang|texts)/(\w+)\.(?:lang|json|txt)$"
RE_SOUND = rf"{ROOT_ASSETS}sounds/(\w+)/.*?\.ogg$"

ROOT_DATA = "data/minecraft/"
DATA_NAME = r"(.*?/)?[^_]\w*\.json$"
RE_LOOT_TABLE = rf"{ROOT_DATA}loot_table/(\w+)/{DATA_NAME}"
RE_RECIPE = rf"{ROOT_DATA}recipe/{DATA_NAME}"
# RE_DATA = rf"{ROOT_DATA}(dimension_type|villager_trade)/{DATA_NAME}"

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
STEP_COUNT = 10
STEP_SIZE = BAR_COUNT / STEP_COUNT

def get_seed() -> int:
    return int(time.time() * 1000) % MAX_SEED


def get_resource_path(path: str) -> str:
    return os.path.join(os.path.dirname(__file__), "resources", path)


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
        self.layout.setSpacing(20)

        self.setWindowIcon(QIcon(get_resource_path("icon.png")))
        self.setWindowTitle(f"Minecraft Asset Randomizer {APP_VERSION}")

        self.global_options(self.layout)

        self.tabs = qw.QTabWidget(self)
        self.tabs.setStyleSheet("QTabWidget::pane { border: 0; }")
        self.layout.addWidget(self.tabs)

        # Randomizer options
        self.resourcepack_options, resourcepack_layout = self.make_options_layout()
        self.texture_options(resourcepack_layout)
        self.sound_options(resourcepack_layout)
        self.text_options(resourcepack_layout)
        self.model_options(resourcepack_layout)
        self.tabs.addTab(self.resourcepack_options, "Resource pack")

        self.datapack_options, datapack_layout = self.make_options_layout()
        self.loot_table_options(datapack_layout)
        self.recipe_options(datapack_layout)
        self.tabs.addTab(self.datapack_options, "Data pack")

        self.tabs.currentChanged.connect(self.on_change_tab)

        # Controls
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


    def make_options_layout(self):
        options_scroll = qw.QScrollArea()
        options = qw.QWidget()
        options_layout = qw.QVBoxLayout(options)
        options_layout.setSpacing(10)
        options_scroll.setWidget(options)
        options_scroll.setWidgetResizable(True)

        type_help = qw.QWidget()
        type_help_layout = qw.QHBoxLayout(type_help)
        type_help_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.addWidget(type_help)

        type_help_icon = qw.QLabel(
            pixmap=qw.QApplication.style()\
                .standardIcon(qw.QStyle.StandardPixmap.SP_MessageBoxInformation)\
                .pixmap(16, 16)
        )
        type_help_icon.setFixedWidth(20)
        type_help_layout.addWidget(type_help_icon)
        type_help_layout.addWidget(
            qw.QLabel(
                "Double click items to include/exclude them.\n"
                "All subtypes of an asset are shuffled together.\n"
                "If you want to keep some shuffled separately, "
                "select them and click the lock button.",
                wordWrap=True,
            )
        )

        return options_scroll, options_layout


    def on_change_tab(self, index: int):
        self.generate.setText(f"Generate {"resource" if index == 0 else "data"} pack")


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


    def progress_step(self, description: str, subtotal: int = 0, start: bool = False):
        self.progress_value = 0 if start else (self.progress_value + STEP_SIZE)
        self.progress_subtotal = subtotal
        self.progress_desc = description
        self.update_progress()


    def progress_substep(self, subvalue: int):
        self.progress_subvalue = subvalue
        self.update_progress()


    def global_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Global")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        version_inputs = qw.QWidget()
        version_layout = qw.QHBoxLayout(version_inputs)
        version_layout.setContentsMargins(0, 0, 0, 0)

        self.version = qw.QComboBox()
        self.version.addItems([".".join([str(n) for n in v]) for v in VERSIONS])
        version_layout.addWidget(self.version)
        version_layout.setStretch(0, 1)

        self.cache_assets = qw.QCheckBox("Cache downloaded assets")
        version_layout.addWidget(self.cache_assets)

        layout.addRow(qw.QLabel("Version"), version_inputs)

        seed_inputs = qw.QWidget()
        seed_layout = qw.QHBoxLayout(seed_inputs)
        seed_layout.setContentsMargins(0, 0, 0, 0)

        self.seed = qw.QSpinBox(minimum=0, maximum=MAX_SEED, value=get_seed())
        self.seed.setDisabled(True)
        seed_layout.addWidget(self.seed)
        seed_layout.setStretch(0, 1)

        self.rand_seed = qw.QCheckBox("Randomize seed")
        self.rand_seed.setChecked(True)
        self.rand_seed.stateChanged.connect(self.on_rand_seed_checked)
        seed_layout.addWidget(self.rand_seed)

        layout.addRow(qw.QLabel("Seed"), seed_inputs)


    def on_rand_seed_checked(self, checked):
        self.seed.setDisabled(checked)


    def texture_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Textures")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.texture_types = [*DEFAULT_TEXTURE_TYPES]
        self.locked_texture_types = []

        type_select = IncludeList([*TEXTURE_TYPES.keys()], self.texture_types, self.locked_texture_types)
        layout.addRow(type_select)

        transparency_inputs = qw.QWidget()
        transparency_layout = qw.QHBoxLayout(transparency_inputs)
        transparency_layout.setContentsMargins(0, 0, 0, 0)

        self.transparency_bins = qw.QSpinBox(minimum=2, maximum=16, value=8)
        transparency_layout.addWidget(self.transparency_bins)
        transparency_layout.setStretch(0, 1)

        self.match_transparency = qw.QCheckBox("Match transparency")
        self.match_transparency.setChecked(True)
        self.match_transparency.stateChanged.connect(self.match_transparency_changed)
        transparency_layout.addWidget(self.match_transparency)

        layout.addRow(qw.QLabel("Transparency levels"), transparency_inputs)

        palette_inputs = qw.QWidget()
        palette_layout = qw.QHBoxLayout(palette_inputs)
        palette_layout.setContentsMargins(0, 0, 0, 0)

        self.palette_size = qw.QSpinBox(minimum=2, maximum=64, value=16)
        self.palette_size.setDisabled(True)
        palette_layout.addWidget(self.palette_size)
        palette_layout.setStretch(0, 1)

        self.keep_palette = qw.QCheckBox("Keep color palette")
        self.keep_palette.stateChanged.connect(self.keep_palette_changed)
        palette_layout.addWidget(self.keep_palette)

        layout.addRow(qw.QLabel("Palette size"), palette_inputs)


    def match_transparency_changed(self, checked):
        self.transparency_bins.setEnabled(checked)


    def keep_palette_changed(self, checked):
        self.palette_size.setEnabled(checked)


    def sound_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Sounds")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.sound_types = [*SOUND_TYPES.keys()]
        self.locked_sound_types = [*DEFAULT_LOCKED_SOUND_TYPES]

        type_select = IncludeList([*SOUND_TYPES.keys()], self.sound_types, self.locked_sound_types)
        layout.addRow(type_select)


    def text_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Text")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.text_types = []
        self.locked_text_types = []

        type_select = IncludeList([*TEXT_TYPES.keys()], self.text_types, self.locked_text_types)
        layout.addRow(type_select)


    def model_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Models")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.model_types = []
        self.locked_model_types = []

        type_select = IncludeList([*MODEL_TYPES.keys()], self.model_types, self.locked_model_types)
        layout.addRow(type_select)


    def loot_table_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Loot tables")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.loot_table_types = [*LOOT_TABLES_TYPES.keys()]
        self.locked_loot_table_types = []

        type_select = IncludeList([*LOOT_TABLES_TYPES.keys()], self.loot_table_types, self.locked_loot_table_types)
        layout.addRow(type_select)


    def recipe_options(self, parent: qw.QLayout):
        group = qw.QGroupBox("Recipes")
        layout = qw.QFormLayout(group)
        parent.addWidget(group)

        self.recipe_types = [*RECIPE_TYPES.keys()]
        self.locked_recipe_types = []

        type_select = IncludeList([*RECIPE_TYPES.keys()], self.recipe_types, self.locked_recipe_types)
        layout.addRow(type_select)


    # def data_options(self, parent: qw.QLayout):
    #     group = qw.QGroupBox("Other")
    #     layout = qw.QFormLayout(group)
    #     parent.addWidget(group)
    #
    #     info = qw.QWidget()
    #     info_layout = qw.QHBoxLayout(info)
    #     info_layout.setContentsMargins(0, 0, 0, 0)
    #     layout.addWidget(info)
    #
    #     info_icon = qw.QLabel(
    #         pixmap=qw.QApplication.style() \
    #             .standardIcon(qw.QStyle.StandardPixmap.SP_MessageBoxInformation) \
    #             .pixmap(16, 16)
    #     )
    #     info_icon.setFixedWidth(20)
    #     info_layout.addWidget(info_icon)
    #     info_layout.addWidget(
    #         qw.QLabel(
    #             "These subtypes are grouped for convenience.\n"
    #             "They are forced to be shuffled separately.",
    #             wordWrap=True,
    #         )
    #     )
    #
    #     self.data_types = [*DATA_TYPES.keys()]
    #
    #     type_select = IncludeList([*DATA_TYPES.keys()], self.data_types, self.data_types)
    #     layout.addRow(type_select)


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
        self.resourcepack_options.widget().setDisabled(running)
        self.datapack_options.widget().setDisabled(running)
        self.tabs.tabBar().setDisabled(running)
        resourcepack = self.tabs.currentIndex() == 0
        self.generate.setText(
            "Cancel generation" if running else
            f"Generate {"resource" if resourcepack else "data"} pack"
        )

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


    @staticmethod
    def get_types_locked(all_types: dict[str, list[str]], types: list[str], locked: list[str]):
        return (
            [t for label in types for t in all_types[label]],
            [t for label in locked for t in all_types[label]]
        )

    
    def run(self):
        self.running.emit(True)

        version_str = self.win.version.currentText()
        version = [int(n) for n in version_str.split(".")]
        if self.win.rand_seed.isChecked():
            seed = get_seed()
            self.seed.emit(seed)
        else:
            seed = self.win.seed.value()
        random.seed(seed)

        resourcepack = self.win.tabs.currentIndex() == 0
        self.pack_name = f"{"resource" if resourcepack else "data"}pack_{version_str}_{seed}.zip"

        cached_archive_name = f"assets_{version_str}.zip"
        has_cached = os.path.exists(cached_archive_name)
        use_cached = self.win.cache_assets.isChecked() or has_cached
        archive_name = cached_archive_name if use_cached else "assets.zip"

        texture_types, locked_texture_types = self.get_types_locked(
            TEXTURE_TYPES, self.win.texture_types, self.win.locked_texture_types
        )
        sound_types, locked_sound_types = self.get_types_locked(
            SOUND_TYPES, self.win.sound_types, self.win.locked_sound_types
        )
        text_types, locked_text_types = self.get_types_locked(
            TEXT_TYPES, self.win.text_types, self.win.locked_text_types
        )
        model_types, locked_model_types = self.get_types_locked(
            MODEL_TYPES, self.win.model_types, self.win.locked_model_types
        )

        loot_table_types, locked_loot_table_types = self.get_types_locked(
            LOOT_TABLES_TYPES, self.win.loot_table_types, self.win.locked_loot_table_types
        )
        recipe_types, locked_recipe_types = self.get_types_locked(
            RECIPE_TYPES, self.win.recipe_types, self.win.locked_recipe_types
        )
        # data_types, locked_data_types = self.get_types_locked(
        #     DATA_TYPES, self.win.data_types, self.win.data_types
        # )

        # Cleanup
        self.step.emit(Step("Preparing download...", start=True))
        self.clean(True)

        # Download asset pack
        if not has_cached:
            self.step.emit(Step(f"Downloading assets for version {version_str}..."))

            url = f"https://api.github.com/repos/InventivetalentDev/minecraft-assets/zipball/tags/{version_str}"
            with requests.get(url) as r, open(archive_name, "wb") as f:
                if self.stopped:
                    self.clean(True)
                    return
                f.write(r.content)
        else:
            self.step.emit(Step(f"Using cached assets assets for version {version_str}"))

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

            match = re.search(re_include, path, re.IGNORECASE)
            if match and (re_exclude is None or not re.search(re_exclude, path, re.IGNORECASE)):
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

        with ZipFile(archive_name, "r") as archive:
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
                    resourcepack and (
                        add_asset(path, "texture", RE_TEXTURE, texture_types, locked_texture_types) or
                        add_asset(path, "sound", RE_SOUND, sound_types, locked_sound_types) or
                        add_asset(path, "text", RE_TEXT, text_types, locked_text_types) or
                        add_asset(path, "model", RE_MODEL, model_types, locked_model_types, RE_MODEL_BLACKLIST)
                    )
                ) or (
                    not resourcepack and (
                        add_asset(path, "loot_table", RE_LOOT_TABLE, loot_table_types, locked_loot_table_types) or
                        add_asset(path, "recipe", RE_RECIPE, len(recipe_types) > 0)
                    )
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

            strings = pd.DataFrame({
                "path": pd.Series(dtype=str),
                "key": pd.Series(dtype=str),
                "value": pd.Series(dtype=str),
                "placeholders": pd.Series(dtype=int),
            })

            # Read .json lang files
            for _, lang in assets[(assets["type"] == "text") & assets["path"].str.endswith(".json")].iterrows():
                path = lang["path"]
                with open(path, encoding="utf-8") as f:
                    lang_ts = json.load(f)

                df = pd.DataFrame({
                    "key": lang_ts.keys(),
                    "value": [v.strip() for v in lang_ts.values()],
                })
                df["path"] = path
                strings = pd.concat([strings, df], ignore_index=True)

            # Read .lang lang files
            for _, lang in assets[(assets["type"] == "text") & assets["path"].str.endswith(".lang")].iterrows():
                path = lang["path"]
                with open(path, encoding="utf-8") as f:
                    lang_lines = f.readlines()

                split = [l.split("=", 1) for l in lang_lines if "=" in l]

                df = pd.DataFrame({
                    "key": [s[0] for s in split],
                    "value": [s[1].strip() for s in split]
                })
                df["path"] = path
                strings = pd.concat([strings, df], ignore_index=True)

            # Read .txt files
            for _, lang in assets[(assets["type"] == "text") & assets["path"].str.endswith(".txt")].iterrows():
                path = lang["path"]
                with open(path, encoding="utf-8") as f:
                    txt_lines = f.readlines()

                df = pd.DataFrame({"value": [l.strip() for l in txt_lines]})
                df["path"] = path
                strings = pd.concat([strings, df], ignore_index=True)

            strings["placeholders"] = strings["value"].map(
                lambda v: len([m for m in re.findall(r"%(?:%|s|[0-9]+)", v) if m != "%%"])
            )

            if not strings.empty:
                shuffled_values = pd.concat(
                    group.sample(frac=1, random_state=seed).reset_index().set_index(group.index)
                    for _, group in strings.groupby("placeholders")
                )
                strings["new_value"] = shuffled_values["value"]

                for path, content in strings.groupby("path"):
                    with open(path, "w", encoding="utf-8") as f:
                        if path.endswith(".json"):
                            json.dump({ts["key"]: ts["new_value"] for _, ts in content.iterrows()}, f, indent=2)
                        elif path.endswith(".lang"):
                            f.writelines([ts["key"] + "=" + ts["new_value"] + "\n" for _, ts in content.iterrows()])
                        else:
                            f.writelines([ts["new_value"] + "\n" for _, ts in content.iterrows()])

        # Shuffle recipes
        if recipe_types:
            self.step.emit(Step("Shuffling recipe results..."))

            recipes = pd.DataFrame({
                "path": pd.Series(dtype=str),
                "content": pd.Series(dtype=str),
                "type": pd.Series(dtype=str),
                "output": pd.Series(dtype=str),
            })

            for i, recipe in assets[assets["type"] == "recipe"].iterrows():
                path = recipe["path"]
                with open(path, encoding="utf-8") as f:
                    content = json.load(f)

                recipe_type = content["type"][10:]
                if recipe_type not in recipe_types or "result" not in content:
                    assets.drop([i], inplace=True)
                    continue

                if recipe_type in locked_recipe_types:
                    recipe_type = None

                df = pd.DataFrame({
                    "path": [path],
                    "content": [content],
                    "type": [recipe_type],
                    "result": [content["result"]],
                })
                recipes = pd.concat([recipes, df], ignore_index=True)

            if not recipes.empty:
                shuffled_recipes = pd.concat(
                    group.sample(frac=1, random_state=seed).reset_index().set_index(group.index)
                    for _, group in recipes.groupby("type", dropna=False)
                )
                recipes["new_result"] = shuffled_recipes["result"]

                for _, recipe in recipes.iterrows():
                    with open(recipe["path"], "w", encoding="utf-8") as f:
                        json.dump({**recipe["content"], "result": recipe["new_result"]}, f, indent=2)


        # Shuffle assets
        self.step.emit(Step("Shuffling assets..."))
        criterion = ["type", "subtype"]
        if self.win.match_transparency.isChecked():
            criterion.append("transparency")

        to_shuffle = assets[~(assets["type"].isin(["text", "recipe"]))]
        if not to_shuffle.empty:
            shuffled = pd.concat(
                group.sample(frac=1, random_state=seed).reset_index().set_index(group.index)
                for _, group in to_shuffle.groupby(criterion, dropna=False)
            )
            shuffled["new_path"] = shuffled["path"]
            assets = assets.join(shuffled[["new_path"]])

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

        # Generate resource pack zip
        with zipfile.ZipFile(os.path.join("..", self.pack_name), "w") as pack:
            # Rename files
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
                    has_new_path = "new_path" in asset and not pd.isna(asset["new_path"])
                    target = asset["new_path"] if has_new_path else asset["path"]
                    pack.write(asset["path"], target)

            # Generate pack.mcmeta
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

            # Generate pack.png
            with Image.open(get_resource_path("tiles.png")) as tiles:
                icons_rp = [tiles.crop((i, 0, i + 32, 32)) for i in range(0, 128, 32)]
                icons_dp = [tiles.crop((i, 32, i + 32, 64)) for i in range(0, 64, 32)]
                small_num = [tiles.crop((i, 64, i + 10, 80)) for i in range(0, 100, 10)]
                big_num = [tiles.crop((i, 80, i + 12, 112)) for i in range(0, 120, 12)]
                dot = tiles.crop((120, 80, 128, 112))
                mosaics = [tiles.crop((i, 112, i + 16, 128)) for i in range(0, 64, 16)]

            hue = random.random()
            bg = tuple(int(n * 255) for n in colorsys.hsv_to_rgb(math.fmod(hue - 0.1, 1), 0.5, 0.6))
            fg = tuple(int(n * 255) for n in colorsys.hsv_to_rgb(math.fmod(hue + 0.1, 1), 0.5, 0.2))
            fg_shadow = tuple(int(n * 255) for n in colorsys.hsv_to_rgb(math.fmod(hue + 0.05, 1), 0.4, 0.4))

            with Image.new("RGB", (128, 128), bg) as icon:
                for x in range(0, 128, 16):
                    bg2 = tuple(int(n * 255) for n in colorsys.hsv_to_rgb(hue - 0.1 + x / (128 * 8), 0.5, 0.6))
                    for y in range(0, 128, 16):
                        t = mosaics[random.randint(0, len(mosaics) - 1)]
                        tr = t.rotate(90 * random.randint(0, 3))
                        icon.paste(colorize(tr, bg2), (x, y), tr)

                with Image.new("RGBA", (128, 128), (0, 0, 0, 0)) as mask:
                    mask.paste((0, 0, 0, 140), (0, 0, 128, 32))
                    mask.paste((0, 0, 0, 140), (0, 80, 128, 128))
                    icon.paste((bg[0], bg[1], bg[2]), (0, 0, 128, 128), mask)

                with Image.new("RGBA", (128, 128)) as text:
                    x = 0
                    if resourcepack:
                        for i, t in enumerate([texture_types, sound_types, text_types, model_types]):
                            if len(t) > 0:
                                text.paste(icons_rp[i], (x, 0))
                                x += 32
                    else:
                        for i, t in enumerate([loot_table_types, recipe_types]):
                            if len(t) > 0:
                                text.paste(icons_dp[i], (x, 0))
                                x += 32

                    x = 4
                    for c in version_str:
                        if c == ".":
                            text.paste(dot, (x - 2, 80))
                            x += 7
                            continue

                        text.paste(big_num[int(c)], (x, 80))
                        x += 14

                    x = 3
                    for c in str(seed):
                        text.paste(small_num[int(c)], (x, 111))
                        x += 10

                    icon.paste(colorize(text, fg_shadow), (2, 1), text)
                    icon.paste(colorize(text, fg), (0, 0), text)

                icon.save("pack.png")

            pack.write("pack.png")

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
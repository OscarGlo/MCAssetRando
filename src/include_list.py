import PySide6.QtCore as qc
import PySide6.QtWidgets as qw


class IncludeList(qw.QWidget):
    def __init__(self, items: list[str], selected: list[str], locked: list[str]):
        super().__init__()

        self.items = items
        self.selected = selected
        self.locked = locked

        self.setMinimumHeight(130)

        layout = qw.QHBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        excluded = qw.QWidget()
        excluded_layout = qw.QVBoxLayout(excluded)
        layout.addWidget(excluded)

        excluded_label = qw.QLabel("Excluded")
        excluded_label.setFixedHeight(10)
        excluded_layout.addWidget(excluded_label)

        self.excluded_list = qw.QListWidget()
        self.excluded_list.setSelectionMode(qw.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.excluded_list.setStyleSheet("color: gray")
        self.excluded_list.setIconSize(qc.QSize(8, 8))
        self.excluded_list.itemDoubleClicked.connect(self.include_item)
        excluded_layout.addWidget(self.excluded_list)

        controls = qw.QWidget()
        controls_layout = qw.QVBoxLayout(controls)
        layout.addWidget(controls, alignment=qc.Qt.AlignmentFlag.AlignCenter)

        include_all = qw.QPushButton("❱")
        include_all.setFixedSize(30, 30)
        include_all.clicked.connect(self.include_all_clicked)
        controls_layout.addWidget(include_all)

        lock = qw.QPushButton("🔒")
        lock.setFixedSize(30, 30)
        lock.clicked.connect(self.toggle_lock)
        controls_layout.addWidget(lock)

        exclude_all = qw.QPushButton("❰")
        exclude_all.setFixedSize(30, 30)
        exclude_all.clicked.connect(self.exclude_all_clicked)
        controls_layout.addWidget(exclude_all)

        included = qw.QWidget()
        included_layout = qw.QVBoxLayout(included)
        layout.addWidget(included)

        included_label = qw.QLabel("Included")
        included_label.setFixedHeight(10)
        included_layout.addWidget(included_label)

        self.included_list = qw.QListWidget()

        self.included_list.setSelectionMode(qw.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.included_list.setIconSize(qc.QSize(8, 8))
        self.included_list.itemDoubleClicked.connect(self.exclude_item)
        included_layout.addWidget(self.included_list)

        self.update_lists()

    def update_lists(self):
        self.included_list.clear()
        self.excluded_list.clear()

        for label in self.items:
            item = qw.QListWidgetItem()
            if label in self.locked:
                item.setText("🔒 " + label)
            else:
                item.setText(label)
            if label in self.selected:
                self.included_list.addItem(item)
            else:
                self.excluded_list.addItem(item)


    def toggle_lock(self):
        for item in self.included_list.selectedItems():
            name = item.text().strip("🔒 ")
            if name in self.locked:
                self.locked.remove(name)
            else:
                self.locked.append(name)
        self.update_lists()


    def include_item(self, item):
        self.selected.append(item.text().strip("🔒 "))
        self.update_lists()


    def include_all_clicked(self):
        self.selected.clear()
        self.selected.extend(self.items)
        self.update_lists()


    def exclude_item(self, item):
        name = item.text().strip("🔒 ")
        self.selected.remove(name)
        self.locked.remove(name)
        self.update_lists()


    def exclude_all_clicked(self):
        self.selected.clear()
        self.locked.clear()
        self.update_lists()
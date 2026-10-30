"""Boîtes de dialogue : connexion, ouverture d'un projet, rapport de synchronisation."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from .._sdk import siamois_sdk  # noqa: F401
from siamois_sdk import SiamoisClient, SiamoisError
from ..core import spatial_model as sm


class LoginDialog(QDialog):
    def __init__(self, parent=None, url: str = "", email: str = ""):
        super().__init__(parent)
        self.setWindowTitle("SIAMOIS – Connexion")
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://siamois.example.org/siamois")
        self.email = QLineEdit(email)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.error = QLabel()
        self.error.setStyleSheet("color: #b00020")
        self.error.setWordWrap(True)
        form = QFormLayout()
        form.addRow("URL du serveur", self.url)
        form.addRow("Email", self.email)
        form.addRow("Mot de passe", self.password)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_login)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.error)
        lay.addWidget(buttons)
        self.client: Optional[SiamoisClient] = None

    def _try_login(self) -> None:
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            client = SiamoisClient(self.url.text().strip())
            client.login(self.email.text().strip(), self.password.text())
        except SiamoisError as exc:
            self.error.setText(exc.user_message)
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.password.clear()  # le mot de passe n'est jamais conservé
        self.client = client
        self.accept()


class OpenProjectDialog(QDialog):
    """Choix de l'organisation puis du projet."""

    def __init__(self, client: SiamoisClient, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SIAMOIS – Ouvrir un projet")
        self.client = client
        self.org = QComboBox()
        self.project = QComboBox()
        self.error = QLabel()
        self.error.setStyleSheet("color: #b00020")
        form = QFormLayout()
        form.addRow("Organisation", self.org)
        form.addRow("Projet", self.project)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Ouvrir le projet")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.error)
        lay.addWidget(buttons)
        self.org.currentIndexChanged.connect(self._load_projects)
        try:
            for o in client.organizations().items:
                self.org.addItem(o.name, o.id)
        except SiamoisError as exc:
            self.error.setText(exc.user_message)

    def _load_projects(self) -> None:
        from qgis.PyQt.QtWidgets import QApplication
        self.project.clear()
        org_id = self.org.currentData()
        if org_id is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            projects = list(self.client.iter_all(lambda **kw: self.client.projects(organization_id=org_id, **kw)))
            for p in projects:
                self.project.addItem(f"{p.name} ({p.full_identifier})" if p.full_identifier else p.name, p.id)
            self.error.setText("" if projects else "Aucun projet accessible dans cette organisation.")
        except SiamoisError as exc:
            self.error.setText(exc.user_message)
        finally:
            QApplication.restoreOverrideCursor()

    def selection(self):
        return self.org.currentData(), self.project.currentData()


class ReportDialog(QDialog):
    """Tableau de rapport (validation / synchronisation) ; les conflits proposent un choix par ligne."""

    LOCAL, SERVER = "Garder ma version", "Garder la version serveur"

    def __init__(self, title: str, summary: str, rows: List[Dict], parent=None, apply_label: Optional[str] = None):
        """rows : {layer, label, column, message, level("ok"|"warn"|"error"|"conflict"), key}."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(summary))
        self.table = QTableWidget(len(rows), 4)
        self.table.setHorizontalHeaderLabels(["Couche", "Élément", "Colonne / action", "Message"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.choices: Dict[str, QComboBox] = {}
        icon = {"ok": "✅", "warn": "⚠️", "error": "❌", "conflict": "⚠️"}
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(r.get("layer", "")))
            self.table.setItem(i, 1, QTableWidgetItem(f"{icon.get(r['level'], '')} {r['label']}"))
            if r["level"] == "conflict":
                box = QComboBox()
                box.addItems([self.LOCAL, self.SERVER])
                self.table.setCellWidget(i, 2, box)
                self.choices[r["key"]] = box
            else:
                self.table.setItem(i, 2, QTableWidgetItem(r.get("column", "")))
            self.table.setItem(i, 3, QTableWidgetItem(r.get("message", "")))
        lay.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        if apply_label:
            buttons.addButton(apply_label, QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def decisions(self) -> Dict[str, str]:
        return {k: ("local" if b.currentText() == self.LOCAL else "server") for k, b in self.choices.items()}


class SpatialConfigDialog(QDialog):
    """Répartition des UE dans les tables du modèle spatial : table par type, et géométrie par défaut."""

    GEOMETRIES = [("Polygone", sm.POLYGON), ("Ligne", sm.LINE), ("Point", sm.POINT)]

    def __init__(self, parent=None, type_labels: Optional[List[str]] = None, config: Optional[sm.SpatialConfig] = None):
        super().__init__(parent)
        self.setWindowTitle("SIAMOIS – Répartition dans le modèle spatial")
        self.resize(560, 460)
        config = config or sm.SpatialConfig()
        self._labels = list(type_labels or [])

        self.geometry_box = QComboBox()
        for text, value in self.GEOMETRIES:
            self.geometry_box.addItem(text, value)
        self.geometry_box.setCurrentIndex(max(0, self.geometry_box.findData(
            config.default_geometry.get(sm.KIND_RU, sm.POLYGON))))
        form = QFormLayout()
        form.addRow("Géométrie par défaut d'une UE sans géométrie", self.geometry_box)

        self.table = QTableWidget(len(self._labels), 2)
        self.table.setHorizontalHeaderLabels(["Type d'UE", "Table du modèle"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self._boxes: List[QComboBox] = []
        for row, label in enumerate(self._labels):
            item = QTableWidgetItem(label)
            item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, item)
            box = QComboBox()
            box.addItem("Automatique (selon la géométrie)", sm.AUTO)
            for name in sm.tables_for_kind(sm.KIND_RU):
                box.addItem(f"{name} – {sm.ENTITY_TABLES[name].title}", name)
            box.setCurrentIndex(max(0, box.findData(config.type_tables.get(label, sm.AUTO))))
            self.table.setCellWidget(row, 1, box)
            self._boxes.append(box)

        info = QLabel(
            "Chaque UE est rangée dans une table du modèle spatial. Sans choix pour son type, la table dépend de la "
            "géométrie réelle de l'UE (surface → uo_poly, ligne → ut_ligne, point → ut_point) ; une UE sans géométrie "
            "reçoit la géométrie par défaut ci-dessous. Le mobilier est toujours rangé dans uo_point.")
        info.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(form)
        lay.addWidget(self.table)
        lay.addWidget(buttons)

    def config(self) -> sm.SpatialConfig:
        cfg = sm.SpatialConfig()
        cfg.default_geometry[sm.KIND_RU] = self.geometry_box.currentData()
        cfg.type_tables = {label: box.currentData() for label, box in zip(self._labels, self._boxes)
                           if box.currentData() != sm.AUTO}
        return cfg

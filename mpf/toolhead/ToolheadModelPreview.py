"""Isolated, demand-rendered model preview; visible surfaces and picking share a camera."""
from __future__ import annotations

import threading

import numpy as np
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PyQt6.QtQuick import QQuickPaintedItem, QQuickWindow, QSGRendererInterface

from ..geometry.ToolheadGeometry import camera_projection, pick_projected, visible_triangles, preview_buffer, preview_camera
from .ToolheadPreviewGL import ToolheadPreviewGL


class ToolheadModelPreview(QQuickPaintedItem):
    modelChanged = pyqtSignal()
    pickingChanged = pyqtSignal()
    addingLightChanged = pyqtSignal()
    rendered = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = None
        self._yaw, self._pitch, self._zoom = 35, 25, 1
        self._pan = (0.0, 0.0)
        self._picking = False
        self._adding_light = False
        self._image = self._projection = self._camera = None
        self._error = ""
        self._mesh = None
        self._generation = 0
        self._worker = None
        self._pending = None
        self._gpu = self._packed = None
        self._gpu_failed = False
        self.windowChanged.connect(self._attach_window)
        self.widthChanged.connect(self._request)
        self.heightChanged.connect(self._request)
        self.rendered.connect(self._ready)
        self._attach_window(self.window())

    def _attach_window(self, window):
        if window is not None and self._gpu is None and QQuickWindow.graphicsApi() == QSGRendererInterface.GraphicsApi.OpenGL:
            self._gpu = ToolheadPreviewGL(self)
            self._gpu.failed.connect(self._gpu_error, Qt.ConnectionType.QueuedConnection)
            self._request()

    def _gpu_error(self, message):
        if self._gpu_failed: return
        self._gpu_failed = True
        self._error = message
        self._packed = None
        self._image = self._projection = self._camera = None
        # Keep the child wrapper alive until Qt retires its renderer, but stop
        # scheduling GPU work and retire any packing result still in flight.
        self._gpu.setVisible(False)
        self._generation += 1
        self._pending = None
        self._request()
        self.update()

    def _update_gpu(self):
        if self._gpu is None or self._gpu_failed: return
        self._gpu.setWidth(self.width())
        self._gpu.setHeight(self.height())
        if self._packed is not None:
            self._camera = preview_camera(self._packed[1], self._packed[2], self._yaw, self._pitch,
                                          self.width(), self.height(), self._zoom)
        self._gpu.update()
        self.update()

    @pyqtProperty(QObject, notify=modelChanged)
    def model(self): return self._model

    @model.setter
    def model(self, value):
        if self._model is value: return
        if self._model is not None:
            try:
                self._model.changed.disconnect(self._model_changed)
                self._model.lightingPreviewChanged.disconnect(self._model_changed)
            except RuntimeError: pass  # QML teardown may retire the model first.
        self._model = value
        self._packed = None
        if value is not None:
            value.changed.connect(self._model_changed)
            value.lightingPreviewChanged.connect(self._model_changed)
        self.modelChanged.emit()
        self.resetCamera()

    @pyqtProperty(bool, notify=pickingChanged)
    def picking(self): return self._picking

    @picking.setter
    def picking(self, value):
        if self._picking != bool(value):
            self._picking = bool(value)
            self.pickingChanged.emit()
            if not self._picking: self.addingLight = False

    @pyqtProperty(bool, notify=addingLightChanged)
    def addingLight(self): return self._adding_light

    @addingLight.setter
    def addingLight(self, value):
        if self._adding_light != bool(value):
            self._adding_light = bool(value)
            self.addingLightChanged.emit()
            if self._adding_light: self.picking = True

    def _model_changed(self):
        if self._model is not None and self._mesh is not self._model.mesh:
            self.picking = False
            self._image = self._projection = None
            self._packed = None
            self.resetCamera()
        else:
            self._update_gpu()
            self.update()  # anchor/status changes do not remesh or rerender

    @pyqtSlot()
    def resetCamera(self):
        self._yaw, self._pitch, self._zoom = 35, 25, 1
        self._pan = (0.0, 0.0)
        self._request()

    @pyqtSlot(float, float)
    def orbit(self, dx, dy):
        if self.picking: return
        self._yaw -= dx * .6
        self._pitch = max(-89, min(89, self._pitch + dy * .6))
        self._request()

    @pyqtSlot(float, float)
    def pan(self, dx, dy):
        self._pan = (self._pan[0] + dx, self._pan[1] + dy)
        # Camera translation never changes the model, anchor or mesh buffer.
        self._update_gpu()
        self.update()

    @pyqtSlot(float)
    def zoomBy(self, delta):
        self._zoom = max(.25, min(8, self._zoom * (1.15 if delta > 0 else 1 / 1.15)))
        self._request()

    @pyqtSlot(float, float)
    def pick(self, x, y):
        if not self.picking or self._model is None: return
        gpu = self._gpu is not None and not self._gpu_failed and self._packed is not None
        if gpu:
            # Projection/hit testing is paid only for a click, never a drag.
            projected = camera_projection(self._mesh, self._yaw, self._pitch, self.width(), self.height(), self._zoom)[0]
        else:
            projected = self._projection
        if projected is None: return
        point = pick_projected(self._mesh, projected, x-self._pan[0], y-self._pan[1], nearest=gpu, surface=self.addingLight)
        if point is not None:
            if self.addingLight:
                position, direction, surface = point
                # A visible face must emit into the visible free half-space,
                # even when an STL exporter wrote its winding backwards.
                if self._camera is not None and np.dot(direction, self._camera[1][:, 2]) < 0:
                    direction = tuple(-v for v in direction)
                self._model.pickedLight(position, direction, surface)
            else: self._model.picked(point)
            self.picking = False

    def _request(self):
        if self._model is None or self.width() < 1 or self.height() < 1: return
        gpu = self._gpu is not None and not self._gpu_failed
        if gpu and self._mesh is self._model.mesh and self._packed is not None:
            self._update_gpu()
            return
        self._generation += 1
        self._pending = (self._generation, self._model.mesh, self._yaw, self._pitch,
                         max(1, int(self.width())), max(1, int(self.height())), self._zoom, gpu)
        if self._worker is None: self._start()
        self.update()

    def _start(self):
        args, self._pending = self._pending, None
        if args is None: return
        generation, mesh, yaw, pitch, width, height, zoom, gpu = args

        def build():
            if gpu: return ("gpu", mesh, preview_buffer(mesh))
            projected, centre, matrix, scale = camera_projection(mesh, yaw, pitch, width, height, zoom)
            image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setPen(Qt.PenStyle.NoPen)
            points = projected[:, :, :2]
            ids = visible_triangles(projected, width, height, mesh.colours)
            last_colour = None
            for offset, index in enumerate(ids):
                if offset % 1024 == 0 and generation != self._generation: break
                rgba = tuple(float(v) for v in mesh.colours[index])
                if rgba != last_colour:
                    painter.setBrush(QColor.fromRgbF(*rgba))
                    last_colour = rgba
                painter.drawPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in points[index]]))
            painter.end()
            return (image, projected, mesh, (centre, matrix, scale))

        def render():
            try: result = build()
            except Exception as error: result = "Preview unavailable: " + str(error)[:120]
            try: self.rendered.emit(generation, result)
            except RuntimeError: pass

        self._worker = threading.Thread(target=render, name="MPF model preview", daemon=True)
        self._worker.start()

    def _ready(self, generation, result):
        self._worker = None
        if generation == self._generation:
            if isinstance(result, str):
                self._image = self._projection = self._camera = None
                self._error = result
            elif isinstance(result[0], str) and result[0] == "gpu":
                _, self._mesh, self._packed = result
                self._image = self._projection = None
                self._error = ""
                self._update_gpu()
            else:
                self._image, self._projection, self._mesh, self._camera = result
                self._error = ""
            self.update()
        if self._pending is not None: self._start()

    def paint(self, painter):
        if self._gpu is not None and not self._gpu_failed and self._packed is not None:
            pass  # the GPU child draws the mesh; this item draws only the marker
        elif self._image is not None:
            painter.drawImage(QPointF(*self._pan), self._image)
        else:
            painter.setPen(QColor("#888888"))
            painter.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignCenter, self._error or "Rendering model…")
        if self._camera is not None and self._model is not None and self._model.tip is not None:
            centre, matrix, scale = self._camera
            point = np.sum((np.array(self._model.tip)-centre)[:, None]*matrix, axis=0)
            x, y = point[:2]*scale + (self.width()/2+self._pan[0], self.height()/2+self._pan[1])
            painter.setPen(QPen(QColor("#FF5A00"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(float(x), float(y)), 6, 6)
            painter.drawLine(QPointF(float(x)-10, float(y)), QPointF(float(x)+10, float(y)))
            painter.drawLine(QPointF(float(x), float(y)-10), QPointF(float(x), float(y)+10))

        if self._camera is not None and self._model is not None:
            centre, matrix, scale = self._camera
            for index, light in enumerate(getattr(self._model, "lights", [])):
                point = np.array(light["position"]) - centre
                x, y = (point @ matrix)[:2]*scale + (self.width()/2+self._pan[0], self.height()/2+self._pan[1])
                painter.setPen(QPen(QColor(light["colour"]), 2))
                painter.setBrush(QColor("#202020"))
                painter.drawEllipse(QPointF(float(x), float(y)), 6, 6)
                painter.drawText(QPointF(float(x)+8, float(y)-4), str(index+1))

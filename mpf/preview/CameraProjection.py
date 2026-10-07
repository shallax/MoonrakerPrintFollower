"""Cache camera-only work across banner points and unchanged hover polls."""


class CameraProjection:
    def __init__(self):
        self._key = self._matrix = self._ray_key = self._ray = None

    @staticmethod
    def signature(camera):
        methods = ("getWorldTransformation", "getProjectionMatrix", "getViewportWidth", "getViewportHeight", "isPerspective")
        if not all(callable(getattr(camera, method, None)) for method in methods): return None
        return (id(camera), camera.getWorldTransformation().getData().tobytes(),
            camera.getProjectionMatrix().getData().tobytes(), camera.getViewportWidth(),
            camera.getViewportHeight(), camera.isPerspective())

    def projector(self, camera):
        key = self.signature(camera)
        if key is None or not callable(getattr(camera, "getProjectToViewMatrix", None)):
            return camera.projectToViewport
        if key != self._key:
            # Cura's per-point projectToViewport inverts the same world matrix
            # each time. Preserve its exact projection convention, once/camera.
            self._matrix = camera.getProjectToViewMatrix()
            self._key = key
        matrix = self._matrix
        width, height, perspective = key[-3:]
        def project(position):
            point = position.preMultiply(matrix)
            if perspective and point.z != 0: point /= point.z * 2.0
            return point.x * width / 2.0, point.y * height / 2.0
        return project

    def ray(self, camera, x, y, width, height):
        signature = self.signature(camera)
        key = signature, x, y, width, height
        if signature is None or key != self._ray_key:
            self._ray = camera.getRay(2.0 * x / width - 1.0, 2.0 * y / height - 1.0)
            self._ray_key = key
        return self._ray

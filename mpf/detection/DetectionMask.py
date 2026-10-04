"""Exclude source pixels before resize; all geometry stays in raw-image space."""

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QImage, QPainter, QPainterPath


class RegionResolutionError(ValueError):
    """This camera sample cannot resolve its configured monitored regions."""


def region_path(regions, width=1.0, height=1.0):
    result = QPainterPath()
    for region in regions:
        path = QPainterPath()
        path.moveTo(QPointF(region[0][0] * width, region[0][1] * height))
        for x, y in region[1:]:
            path.lineTo(QPointF(x * width, y * height))
        path.closeSubpath()
        result = result.united(path)
    return result


def masked_image(image, regions):
    if not regions:
        return image
    path = region_path(regions, image.width(), image.height())
    if path.boundingRect().width() < 2 or path.boundingRect().height() < 2:
        raise RegionResolutionError("Monitored regions are too small at this camera resolution; enlarge or reset them")
    masked = QImage(image.size(), QImage.Format.Format_RGB888)
    masked.fill(0)
    painter = QPainter(masked)
    painter.setClipPath(path)
    painter.drawImage(0, 0, image)
    painter.end()
    return masked


def cropped_masked_image(image, regions):
    """Return masked ROI pixels and their integer bounds in the source image.

    All polygons share one crop. Rounding outwards preserves edge pixels;
    gaps and concavities remain masked, even inside the enclosing rectangle.
    """
    masked = masked_image(image, regions)
    bounds = image.rect()
    if regions:
        bounds = region_path(regions, image.width(), image.height()).boundingRect().toAlignedRect().intersected(bounds)
        return masked.copy(bounds), bounds
    return masked, bounds


def box_intersects_regions(box, path):
    candidate = QPainterPath()
    candidate.addRect(QRectF(*box))
    intersection = candidate.intersected(path)
    return not intersection.isEmpty() and not intersection.boundingRect().isEmpty()

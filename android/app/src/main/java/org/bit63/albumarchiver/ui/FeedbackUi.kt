package org.bit63.albumarchiver.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.exifinterface.media.ExifInterface
import org.bit63.albumarchiver.data.PageWarning
import org.bit63.albumarchiver.data.ShotRect
import org.bit63.albumarchiver.data.WarningLine
import java.io.File

val WarningRed = Color(0xFFE53935)
val UncoveredCyan = Color(0xFF26C6DA)

/** The red dot on the top-right corner of a warned page's pictures (overlap coverage Requirement 4.3). */
@Composable
fun BoxScope.WarningDot(size: Dp = 10.dp, inset: Dp = 4.dp, tag: String = "warningDot") {
    Box(
        Modifier
            .align(Alignment.TopEnd)
            .padding(inset)
            .size(size)
            .clip(CircleShape)
            .background(WarningRed)
            .border(1.dp, Color.White, CircleShape)
            .testTag(tag),
    )
}

/** The size of a JPEG as it is displayed: its pixel size, turned by its EXIF orientation. */
fun displayedSize(file: File): Pair<Int, Int>? {
    val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(file.path, o)
    if (o.outWidth <= 0 || o.outHeight <= 0) return null
    val orientation = try {
        ExifInterface(file).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
    } catch (e: Exception) {
        ExifInterface.ORIENTATION_NORMAL
    }
    val turned = orientation in setOf(
        ExifInterface.ORIENTATION_ROTATE_90, ExifInterface.ORIENTATION_ROTATE_270,
        ExifInterface.ORIENTATION_TRANSPOSE, ExifInterface.ORIENTATION_TRANSVERSE,
    )
    return if (turned) o.outHeight to o.outWidth else o.outWidth to o.outHeight
}

/**
 * Rectangles over an image shown with `ContentScale.Fit` inside a box of the
 * canvas's size: the image's letterboxed placement is recomputed here from
 * [imageAspect] (width over height), so the rectangles land on the photo
 * whatever the box's shape. Drawn inside the same transformed box as the
 * image, they zoom and pan with it (Requirement 4.5).
 */
@Composable
fun WarningRects(rects: List<ShotRect>, imageAspect: Float, modifier: Modifier = Modifier) {
    if (rects.isEmpty() || imageAspect <= 0f) return
    Canvas(modifier.fillMaxSize().testTag("warningRects")) {
        val boxAspect = size.width / size.height
        val fitted = if (imageAspect >= boxAspect) Size(size.width, size.width / imageAspect) else Size(size.height * imageAspect, size.height)
        val origin = Offset((size.width - fitted.width) / 2, (size.height - fitted.height) / 2)
        val stroke = Stroke(width = 2.dp.toPx())
        // Single-covered areas with no glare seen: the same colour, dashed (single-shot glare Requirement 2.8).
        val dashed = Stroke(width = 2.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(8.dp.toPx(), 6.dp.toPx())))
        for (r in rects) {
            val topLeft = Offset(origin.x + r.left * fitted.width, origin.y + r.top * fitted.height)
            val extent = Size((r.right - r.left) * fitted.width, (r.bottom - r.top) * fitted.height)
            if (extent.width <= 0f || extent.height <= 0f) continue
            drawRect(if (r.code == PageWarning.CODE_UNCOVERED) UncoveredCyan else WarningRed, topLeft, extent, style = if (r.dashed) dashed else stroke)
        }
    }
}

/** The warnings bar at the bottom of the review screen, with "Add shots to page N" (Requirements 4.5 to 4.7). */
@Composable
fun WarningsBar(lines: List<WarningLine>, pageNumber: Int, onAddShots: (() -> Unit)?, modifier: Modifier = Modifier) {
    if (lines.isEmpty()) return
    Column(modifier.fillMaxWidth().background(Color(0xCC000000)).padding(horizontal = 12.dp, vertical = 8.dp).testTag("warningsBar")) {
        for (line in lines) {
            Text(
                line.text,
                color = if (line.code == PageWarning.CODE_UNCOVERED) UncoveredCyan else Color(0xFFFF8A80),
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(vertical = 2.dp),
            )
        }
        if (onAddShots != null) {
            TextButton(onClick = onAddShots, modifier = Modifier.align(Alignment.End).testTag("addShots")) {
                Text("Add shots to page $pageNumber")
            }
        }
    }
}

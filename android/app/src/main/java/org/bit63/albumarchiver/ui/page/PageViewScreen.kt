package org.bit63.albumarchiver.ui.page

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import org.bit63.albumarchiver.data.PageWarning
import org.bit63.albumarchiver.data.ShotRect
import org.bit63.albumarchiver.data.ShotState
import org.bit63.albumarchiver.data.plainLines
import org.bit63.albumarchiver.ui.FullPlaceholder
import org.bit63.albumarchiver.ui.LocalContainer
import org.bit63.albumarchiver.ui.ShotFile
import org.bit63.albumarchiver.ui.UncoveredCyan
import org.bit63.albumarchiver.ui.WarningRed
import org.bit63.albumarchiver.ui.WarningsBar
import org.bit63.albumarchiver.ui.displayedSize
import java.io.File

/** Where an image shown with `ContentScale.Fit` lands inside a box: letterboxed, centred. */
fun fittedRect(box: Size, imageAspect: Float): Rect {
    val boxAspect = box.width / box.height
    val fitted = if (imageAspect >= boxAspect) Size(box.width, box.width / imageAspect) else Size(box.height * imageAspect, box.height)
    val origin = Offset((box.width - fitted.width) / 2, (box.height - fitted.height) / 2)
    return Rect(origin, fitted)
}

/**
 * The composed page with each shot's outline and the warning regions on it
 * (overlap coverage spec, Requirements 6.3 to 6.7). Without a preview yet,
 * the first shot stands in with no outlines.
 */
@Composable
fun PageViewScreen(
    albumId: String,
    pageId: String,
    back: () -> Unit,
    openReview: (albumId: String, pageId: String, index: Int) -> Unit,
    toCapture: () -> Unit,
) {
    val container = LocalContainer.current
    val vm: PageViewModel = viewModel(key = "page-$albumId-$pageId") {
        PageViewModel(
            container.repository, container.previewFetcher, albumId, pageId,
            feedback = container.feedbackPoller, uploadRunning = container.uploadRunning,
        )
    }
    val state by vm.state.collectAsStateWithLifecycle()
    val preview by vm.preview.collectAsStateWithLifecycle()
    val page = state.page

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        val file = preview ?: state.shots.firstOrNull()?.takeIf { it.state == ShotState.PRESENT }?.let { File(it.path) }
        val size = remember(file) { file?.let { displayedSize(it) } }
        if (file != null && size != null) {
            val showOverlay = preview != null
            ZoomablePage(
                file = file,
                imageAspect = size.first.toFloat() / size.second,
                outlines = if (showOverlay) state.outlines else emptyList(),
                rects = if (showOverlay) state.rects else emptyList(),
                selectedShotId = state.selectedShotId,
                onTap = { p -> vm.tap(p)?.let { openReview(albumId, pageId, it) } },
            )
        } else {
            FullPlaceholder(if (page == null) "" else "Page ${page.position} has no photos yet")
        }

        Row(
            Modifier.fillMaxWidth().statusBarsPadding().background(Color(0x88000000)),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = back) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back", tint = Color.White)
            }
            val label = when {
                page == null -> ""
                preview != null -> "Page ${page.position}"
                else -> "Page ${page.position} · ${state.stateWord}"
            }
            Text(label, color = Color.White, modifier = Modifier.weight(1f).testTag("pageViewLabel"))
            TextButton(onClick = { openReview(albumId, pageId, state.shotToOpen()) }, modifier = Modifier.testTag("toShot")) {
                Text("Shot", color = Color.White)
            }
        }

        Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().navigationBarsPadding()) {
            if (page != null && state.warnings.isNotEmpty()) {
                WarningsBar(
                    lines = state.warnings.plainLines(),
                    pageNumber = page.position,
                    onAddShots = { vm.addShotsToPage(); toCapture() },
                )
            }
            if (preview == null && page != null && state.shots.isNotEmpty()) {
                Text(
                    "No composed page yet (${state.stateWord}); showing the first photo.",
                    color = Color.White,
                    modifier = Modifier.fillMaxWidth().background(Color(0x88000000)).padding(12.dp).testTag("noPreview"),
                )
            }
        }
    }
}

/** The preview, pinch-zoomed and panned, with the outlines and rectangles drawn in the same transform. */
@Composable
private fun ZoomablePage(
    file: File,
    imageAspect: Float,
    outlines: List<Outline>,
    rects: List<ShotRect>,
    selectedShotId: String?,
    onTap: (PagePoint) -> Unit,
) {
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    var boxSize by remember { mutableStateOf(Size.Zero) }
    Box(
        Modifier
            .fillMaxSize()
            .pointerInput(file) {
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    do {
                        val event = awaitPointerEvent()
                        val pointers = event.changes.count { it.pressed }
                        if (pointers >= 2 || scale > 1f) {
                            val zoom = event.calculateZoom()
                            scale = (scale * zoom).coerceIn(1f, 6f)
                            offset = if (scale == 1f) Offset.Zero else offset + event.calculatePan()
                            event.changes.forEach { if (it.positionChanged()) it.consume() }
                        }
                    } while (event.changes.any { it.pressed })
                }
            }
            .pointerInput(file, imageAspect) {
                // A tap in box pixels, through the zoom and pan, to page fractions.
                detectTapGestures { pos ->
                    val b = Size(size.width.toFloat(), size.height.toFloat())
                    val centre = Offset(b.width / 2, b.height / 2)
                    val unzoomed = (pos - offset - centre) / scale + centre
                    val fit = fittedRect(b, imageAspect)
                    val x = (unzoomed.x - fit.left) / fit.width
                    val y = (unzoomed.y - fit.top) / fit.height
                    if (x in 0f..1f && y in 0f..1f) onTap(PagePoint(x, y))
                }
            }
            .graphicsLayer {
                scaleX = scale
                scaleY = scale
                translationX = offset.x
                translationY = offset.y
            }
            .testTag("pageImage"),
    ) {
        ShotFile(file, Modifier.fillMaxSize())
        Canvas(Modifier.fillMaxSize().testTag("pageOverlay")) {
            boxSize = size
            val fit = fittedRect(size, imageAspect)
            fun at(x: Float, y: Float) = Offset(fit.left + x * fit.width, fit.top + y * fit.height)
            val thin = Stroke(width = 1.5.dp.toPx())
            for (o in outlines) {
                if (o.points.size < 3) continue
                val path = Path().apply {
                    moveTo(at(o.points[0].x, o.points[0].y).x, at(o.points[0].x, o.points[0].y).y)
                    for (p in o.points.drop(1)) { val q = at(p.x, p.y); lineTo(q.x, q.y) }
                    close()
                }
                val selected = o.shotId == selectedShotId
                if (selected) drawPath(path, Color.White.copy(alpha = 0.25f))
                drawPath(path, if (selected) Color.White else Color(0xFFFFD54F), style = if (selected) Stroke(width = 3.dp.toPx()) else thin)
                val cx = o.points.map { it.x }.average().toFloat()
                val cy = o.points.map { it.y }.average().toFloat()
                val c = at(cx, cy)
                drawContext.canvas.nativeCanvas.drawText(
                    o.number.toString(), c.x, c.y,
                    android.graphics.Paint().apply {
                        color = android.graphics.Color.WHITE
                        textSize = 14.dp.toPx()
                        textAlign = android.graphics.Paint.Align.CENTER
                        setShadowLayer(3f, 0f, 0f, android.graphics.Color.BLACK)
                    },
                )
            }
            val stroke = Stroke(width = 2.dp.toPx())
            for (r in rects) {
                val tl = at(r.left, r.top)
                val br = at(r.right, r.bottom)
                if (br.x <= tl.x || br.y <= tl.y) continue
                drawRect(if (r.code == PageWarning.CODE_UNCOVERED) UncoveredCyan else WarningRed, tl, Size(br.x - tl.x, br.y - tl.y), style = stroke)
            }
        }
    }
}

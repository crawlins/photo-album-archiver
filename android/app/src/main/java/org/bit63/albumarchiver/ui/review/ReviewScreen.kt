package org.bit63.albumarchiver.ui.review

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.Shot
import org.bit63.albumarchiver.data.ShotState
import org.bit63.albumarchiver.ui.FullPlaceholder
import org.bit63.albumarchiver.ui.LocalContainer
import org.bit63.albumarchiver.ui.ShotFile
import java.io.File

private sealed interface ReviewDialog {
    data class DeleteShot(val shot: Shot, val page: Page) : ReviewDialog
    data class DeletePage(val page: Page) : ReviewDialog
}

/**
 * One shot full screen with "Page N, shot i of n" (Requirement 11.2): swipe
 * for the other shots of the page, arrows for the adjacent pages, pinch to
 * zoom, and "Delete shot" / "Delete page".
 */
@Composable
fun ReviewScreen(albumId: String, pageId: String, index: Int, back: () -> Unit, toOverview: () -> Unit) {
    val container = LocalContainer.current
    val vm: ReviewViewModel = viewModel(key = "review-$albumId-$pageId-$index") {
        ReviewViewModel(container.repository, container.pageLoader, container.shotFetcher, albumId, pageId, index)
    }
    val position by vm.position.collectAsStateWithLifecycle()
    val page by vm.page.collectAsStateWithLifecycle()
    val shots by vm.shots.collectAsStateWithLifecycle()
    val pages by vm.pages.collectAsStateWithLifecycle()
    val pageLoads by vm.loader.pages.collectAsStateWithLifecycle()
    val shotLoads by vm.loader.shots.collectAsStateWithLifecycle()
    val networkReturns by container.networkReturns.count.collectAsStateWithLifecycle()
    var dialog by remember { mutableStateOf<ReviewDialog?>(null) }
    var menu by remember { mutableStateOf(false) }

    // Nothing left to show: back to the page overview (Requirement 12.6).
    LaunchedEffect(position) { if (position == null) toOverview() }
    val pos = position ?: return
    val current = page

    LaunchedEffect(current?.id, current?.shotsLoaded, networkReturns) { current?.let { vm.loader.ensurePage(it) } }

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        val count = if (current?.shotsLoaded == true) shots.size else (current?.shotCount ?: 0)
        if (shots.isNotEmpty()) {
            val pagerState = rememberPagerState(
                initialPage = pos.index.coerceIn(0, shots.size - 1),
                pageCount = { shots.size },
            )
            // Follow deletions and page changes made by the view model.
            LaunchedEffect(pos.pageId, pos.index, shots.size) {
                val target = pos.index.coerceIn(0, shots.size - 1)
                if (pagerState.currentPage != target) pagerState.scrollToPage(target)
            }
            LaunchedEffect(pagerState) {
                snapshotFlow { pagerState.settledPage }.collect { vm.onShotShown(it) }
            }
            HorizontalPager(
                state = pagerState,
                key = { shots.getOrNull(it)?.id ?: it },
                modifier = Modifier.fillMaxSize().testTag("pager"),
            ) { i ->
                val shot = shots.getOrNull(i) ?: return@HorizontalPager
                LaunchedEffect(shot.id, shot.state, networkReturns) { vm.loader.ensureShot(shot) }
                when {
                    shot.state == ShotState.PRESENT -> ZoomableShot(File(shot.path))
                    shotLoads[shot.id] == LoadState.FAILED -> FullPlaceholder("Couldn't load this shot")
                    else -> FullPlaceholder("Loading…")
                }
            }
        } else {
            val text = when {
                current == null -> ""
                pageLoads[current.id] == LoadState.FAILED -> "Couldn't load page ${current.position}"
                !current.shotsLoaded -> "Loading…"
                else -> "Page ${current.position} has no shots"
            }
            FullPlaceholder(text)
        }

        Row(
            Modifier.fillMaxWidth().statusBarsPadding().background(Color(0x88000000)),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = back) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back", tint = Color.White)
            }
            val label = if (current == null) "" else if (count == 0) "Page ${current.position}" else
                "Page ${current.position}, shot ${(pos.index.coerceIn(0, count - 1)) + 1} of $count"
            Text(label, color = Color.White, modifier = Modifier.weight(1f).testTag("reviewLabel"))
            Box {
                IconButton(onClick = { menu = true }, modifier = Modifier.testTag("reviewMenu")) {
                    Icon(Icons.Default.MoreVert, contentDescription = "More", tint = Color.White)
                }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    val shot = shots.getOrNull(pos.index)
                    if (shot != null && current != null) {
                        DropdownMenuItem(text = { Text("Delete shot") }, onClick = {
                            menu = false
                            dialog = ReviewDialog.DeleteShot(shot, current)
                        })
                    }
                    if (current != null) {
                        DropdownMenuItem(text = { Text("Delete page") }, onClick = {
                            menu = false
                            dialog = ReviewDialog.DeletePage(current)
                        })
                    }
                }
            }
        }

        // Previous- and next-page arrows, hidden at the first and last page (Requirement 11.4).
        val previous = remember(pages, pos) { vm.previousPageId() }
        val next = remember(pages, pos) { vm.nextPageId() }
        Row(Modifier.align(Alignment.BottomCenter).fillMaxWidth().navigationBarsPadding().padding(16.dp)) {
            if (previous != null) {
                IconButton(onClick = { vm.showPage(previous) }, modifier = Modifier.testTag("previousPageArrow")) {
                    Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, contentDescription = "Previous page", tint = Color.White)
                }
            }
            Box(Modifier.weight(1f))
            if (next != null) {
                IconButton(onClick = { vm.showPage(next) }, modifier = Modifier.testTag("nextPageArrow")) {
                    Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = "Next page", tint = Color.White)
                }
            }
        }
    }

    when (val d = dialog) {
        is ReviewDialog.DeleteShot -> {
            val alsoPage = vm.deletingShotDeletesPage(d.page)
            AlertDialog(
                onDismissRequest = { dialog = null },
                title = { Text("Delete this shot of page ${d.page.position}?") },
                text = {
                    Text(
                        if (alsoPage) "It is the only shot of page ${d.page.position}, so the page is deleted too and later pages move up by one."
                        else "The shot is deleted from this phone and from the server."
                    )
                },
                confirmButton = {
                    TextButton(onClick = { vm.deleteShot(d.shot); dialog = null }, modifier = Modifier.testTag("confirmDelete")) {
                        Text("Delete")
                    }
                },
                dismissButton = { TextButton(onClick = { dialog = null }) { Text("Cancel") } },
            )
        }
        is ReviewDialog.DeletePage -> DeletePageDialog(
            page = d.page,
            onConfirm = { vm.deletePage(d.page); dialog = null },
            onDismiss = { dialog = null },
        )
        null -> Unit
    }
}

/**
 * A shot that can be pinch-zoomed to check focus and glare (Requirement 11.5).
 * One-finger drags pan only while zoomed in, so at normal size they reach the
 * pager and swipe between shots.
 */
@Composable
private fun ZoomableShot(file: File) {
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
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
            .graphicsLayer {
                scaleX = scale
                scaleY = scale
                translationX = offset.x
                translationY = offset.y
            }
            .testTag("shotImage"),
    ) {
        ShotFile(file, Modifier.fillMaxSize())
    }
}

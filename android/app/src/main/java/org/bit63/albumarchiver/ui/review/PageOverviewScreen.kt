package org.bit63.albumarchiver.ui.review

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.ui.LocalContainer
import org.bit63.albumarchiver.ui.Placeholder
import org.bit63.albumarchiver.ui.ShotThumbnail

fun shotsLabel(count: Int) = if (count == 1) "1 shot" else "$count shots"

/** Every page of the album with its number, first shot and shot count (Requirement 11.6). */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun PageOverviewScreen(albumId: String, openReview: (albumId: String, pageId: String) -> Unit, back: () -> Unit) {
    val container = LocalContainer.current
    val vm: PageOverviewViewModel = viewModel(key = "overview-$albumId") {
        PageOverviewViewModel(container.repository, container.pageLoader, container.shotFetcher, albumId)
    }
    val cells by vm.cells.collectAsStateWithLifecycle()
    val name by vm.albumName.collectAsStateWithLifecycle()
    val pageLoads by vm.loader.pages.collectAsStateWithLifecycle()
    var toDelete by remember { mutableStateOf<Page?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(name) },
                navigationIcon = {
                    IconButton(onClick = back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") }
                },
            )
        },
    ) { padding ->
        if (cells.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text("No pages yet", modifier = Modifier.testTag("noPages"))
            }
            return@Scaffold
        }
        LazyVerticalGrid(
            columns = GridCells.Adaptive(110.dp),
            modifier = Modifier.fillMaxSize().padding(padding).testTag("pageGrid"),
        ) {
            items(cells, key = { it.page.id }) { cell ->
                val page = cell.page
                LaunchedEffect(page.id, page.shotsLoaded) { vm.loader.ensurePage(page) }
                Column(
                    Modifier
                        .padding(6.dp)
                        .combinedClickable(
                            onClick = { openReview(albumId, page.id) },
                            onLongClick = { toDelete = page },
                        )
                        .testTag("page:${page.position}"),
                ) {
                    val thumbModifier = Modifier.fillMaxWidth().aspectRatio(0.77f).clip(RoundedCornerShape(6.dp))
                    val first = cell.firstShot
                    when {
                        first != null -> ShotThumbnail(first, thumbModifier)
                        pageLoads[page.id] == LoadState.FAILED -> Placeholder("Couldn't load", thumbModifier)
                        else -> Placeholder("", thumbModifier)
                    }
                    Text("Page ${page.position}", style = MaterialTheme.typography.labelLarge)
                    Text(shotsLabel(page.shotCount), style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }

    toDelete?.let { page ->
        DeletePageDialog(
            page = page,
            onConfirm = {
                vm.deletePage(page.id)
                toDelete = null
            },
            onDismiss = { toDelete = null },
        )
    }
}

/** Confirmation naming the page and its shot count (Requirement 12.2). */
@Composable
fun DeletePageDialog(page: Page, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Delete page ${page.position}?") },
        text = { Text("Page ${page.position} and its ${shotsLabel(page.shotCount)} are deleted. Later pages move up by one.") },
        confirmButton = { TextButton(onClick = onConfirm, modifier = Modifier.testTag("confirmDelete")) { Text("Delete") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

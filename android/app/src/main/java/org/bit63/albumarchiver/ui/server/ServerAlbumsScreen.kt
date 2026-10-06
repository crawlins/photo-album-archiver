package org.bit63.albumarchiver.ui.server

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.upload.ServerAlbumSummary
import org.bit63.albumarchiver.ui.LocalContainer
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

private val updatedFormat = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)

fun formatUpdated(text: String): String =
    runCatching { updatedFormat.format(Instant.parse(text).atZone(ZoneId.systemDefault())) }.getOrDefault(text)

/** Every album on the server (Requirement 13). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ServerAlbumsScreen(back: () -> Unit, openSettings: () -> Unit, opened: () -> Unit) {
    val container = LocalContainer.current
    val vm: ServerAlbumsViewModel = viewModel {
        ServerAlbumsViewModel(container.server, container.repository, container.settings, container.importer)
    }
    val state by vm.state.collectAsStateWithLifecycle()
    val refreshing by vm.refreshing.collectAsStateWithLifecycle()
    val message by vm.message.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    var toDelete by remember { mutableStateOf<ServerAlbumSummary?>(null) }

    LaunchedEffect(message) {
        message?.let {
            snackbar.showSnackbar(it)
            vm.clearMessage()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Server albums") },
                navigationIcon = {
                    IconButton(onClick = back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = vm::refresh,
            modifier = Modifier.fillMaxSize().padding(padding),
        ) {
            when (val s = state) {
                ServerAlbumsState.Loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                is ServerAlbumsState.Error -> Column(
                    Modifier.fillMaxSize().padding(32.dp).testTag("serverError"),
                    verticalArrangement = Arrangement.Center,
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(s.problem.message)
                    if (s.problem.toSettings) {
                        Button(onClick = openSettings, modifier = Modifier.padding(top = 16.dp).testTag("toSettings")) { Text("Settings") }
                    } else {
                        Button(onClick = vm::refresh, modifier = Modifier.padding(top = 16.dp)) { Text("Retry") }
                    }
                }
                is ServerAlbumsState.Loaded -> if (s.rows.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("No albums on the server yet") }
                } else {
                    LazyColumn(Modifier.fillMaxSize().testTag("serverAlbums")) {
                        items(s.rows, key = { it.album.id }) { row ->
                            ServerAlbumItem(
                                row = row,
                                onOpen = { vm.open(row.album.id, opened) },
                                onDelete = { toDelete = row.album },
                            )
                            HorizontalDivider()
                        }
                    }
                }
            }
        }
    }

    toDelete?.let { album ->
        AlertDialog(
            onDismissRequest = { toDelete = null },
            title = { Text("Delete ${album.name} from the server?") },
            text = {
                Text(
                    "Its ${if (album.pages == 1) "1 page" else "${album.pages} pages"}, photos, " +
                        "processed pages and PDF are deleted from the server. This cannot be undone."
                )
            },
            confirmButton = {
                TextButton(onClick = { vm.deleteFromServer(album.id); toDelete = null }, modifier = Modifier.testTag("confirmDelete")) {
                    Text("Delete")
                }
            },
            dismissButton = { TextButton(onClick = { toDelete = null }) { Text("Cancel") } },
        )
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ServerAlbumItem(row: ServerAlbumRow, onOpen: () -> Unit, onDelete: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    val a = row.album
    Box {
        Row(
            Modifier
                .fillMaxWidth()
                .combinedClickable(
                    // An album already on this phone is opened and deleted from the drawer (Requirement 13.6).
                    onClick = { if (!row.onPhone) onOpen() },
                    onLongClick = { if (!row.onPhone) menu = true },
                )
                .padding(horizontal = 16.dp, vertical = 12.dp)
                .testTag("serverAlbum:${a.name}"),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(a.name, style = MaterialTheme.typography.titleMedium)
                val size = a.pageSize?.let { PageSize.describe(it) } ?: "no page size"
                Text(
                    "$size · ${a.pages} pages · ${a.shots} shots",
                    style = MaterialTheme.typography.bodySmall,
                )
                Text("Changed ${formatUpdated(a.updated)}", style = MaterialTheme.typography.bodySmall)
            }
            if (row.onPhone) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.testTag("onPhone")) {
                    Icon(Icons.Default.PhoneAndroid, contentDescription = null)
                    Text("On this phone", style = MaterialTheme.typography.labelSmall)
                }
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(text = { Text("Open on this phone") }, onClick = { menu = false; onOpen() })
            DropdownMenuItem(text = { Text("Delete from server") }, onClick = { menu = false; onDelete() })
        }
    }
}

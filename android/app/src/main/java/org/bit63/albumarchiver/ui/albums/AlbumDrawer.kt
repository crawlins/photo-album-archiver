package org.bit63.albumarchiver.ui.albums

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ViewList
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import org.bit63.albumarchiver.data.AlbumSummary

/**
 * The navigation drawer (Requirement 2.2): "New album", the albums on this
 * phone with page count and upload state, "Server albums" and "Settings".
 * Long-press an album for "Edit" and "Delete" (Requirement 2.7).
 */
@Composable
fun AlbumDrawer(
    rows: List<AlbumRow>,
    currentAlbumId: String?,
    onNewAlbum: () -> Unit,
    onSelect: (String) -> Unit,
    onOverview: (String) -> Unit,
    onEdit: (AlbumSummary) -> Unit,
    onDelete: (AlbumSummary) -> Unit,
    onServerAlbums: () -> Unit,
    onSettings: () -> Unit,
) {
    ModalDrawerSheet(modifier = Modifier.testTag("drawer")) {
        Text("Albums", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(16.dp))
        NavigationDrawerItem(
            label = { Text("New album") },
            icon = { Icon(Icons.Default.Add, contentDescription = null) },
            selected = false,
            onClick = onNewAlbum,
            modifier = Modifier.padding(horizontal = 12.dp).testTag("drawerNewAlbum"),
        )
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        LazyColumn(modifier = Modifier.weight(1f)) {
            items(rows, key = { it.summary.id }) { row ->
                AlbumRowItem(
                    row = row,
                    selected = row.summary.id == currentAlbumId,
                    onSelect = { onSelect(row.summary.id) },
                    onOverview = { onOverview(row.summary.id) },
                    onEdit = { onEdit(row.summary) },
                    onDelete = { onDelete(row.summary) },
                )
            }
        }
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        NavigationDrawerItem(
            label = { Text("Server albums") },
            icon = { Icon(Icons.Default.Cloud, contentDescription = null) },
            selected = false,
            onClick = onServerAlbums,
            modifier = Modifier.padding(horizontal = 12.dp).testTag("drawerServerAlbums"),
        )
        NavigationDrawerItem(
            label = { Text("Settings") },
            icon = { Icon(Icons.Default.Settings, contentDescription = null) },
            selected = false,
            onClick = onSettings,
            modifier = Modifier.padding(horizontal = 12.dp).testTag("drawerSettings"),
        )
        Spacer(Modifier.height(12.dp))
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun AlbumRowItem(
    row: AlbumRow,
    selected: Boolean,
    onSelect: () -> Unit,
    onOverview: () -> Unit,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    val s = row.summary
    Box {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .combinedClickable(onClick = onSelect, onLongClick = { menu = true })
                .padding(start = 28.dp, end = 12.dp, top = 8.dp, bottom = 8.dp)
                .testTag("album:${s.name}"),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    s.name,
                    style = MaterialTheme.typography.titleMedium,
                    color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
                )
                val pages = if (s.pageCount == 1) "1 page" else "${s.pageCount} pages"
                Text("$pages · ${row.upload.label}", style = MaterialTheme.typography.bodySmall)
            }
            IconButton(onClick = onOverview) {
                Icon(Icons.AutoMirrored.Filled.ViewList, contentDescription = "Pages of ${s.name}")
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(text = { Text("Edit") }, onClick = { menu = false; onEdit() }, modifier = Modifier.testTag("editAlbum"))
            DropdownMenuItem(text = { Text("Delete") }, onClick = { menu = false; onDelete() }, modifier = Modifier.testTag("deleteAlbum"))
        }
    }
}

/** Confirms deleting an album from the phone, with "Also delete from the server" off by default (Requirement 2.7). */
@Composable
fun DeleteAlbumDialog(summary: AlbumSummary, onConfirm: (alsoOnServer: Boolean) -> Unit, onDismiss: () -> Unit) {
    var alsoOnServer by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Delete ${summary.name}?") },
        text = {
            Column {
                val pages = if (summary.pageCount == 1) "1 page" else "${summary.pageCount} pages"
                Text("Its $pages and their shots are removed from this phone.")
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(
                        checked = alsoOnServer,
                        onCheckedChange = { alsoOnServer = it },
                        modifier = Modifier.testTag("alsoOnServer"),
                    )
                    Text("Also delete from the server")
                }
                if (alsoOnServer) {
                    Text(
                        "The server's photos, processed pages and PDF go too. This cannot be undone.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(alsoOnServer) }, modifier = Modifier.testTag("confirmDelete")) { Text("Delete") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

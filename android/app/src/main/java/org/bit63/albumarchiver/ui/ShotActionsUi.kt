package org.bit63.albumarchiver.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import org.bit63.albumarchiver.data.Shot

private val Accent = Color(0xFFFFC107)

/**
 * A page's thumbnails in the order taken (shot-actions Requirement 1). A long
 * press selects a shot, a long press on the selected one asks for the menu;
 * the selected thumbnail gets a thick border and a check mark and the later
 * ones of its run a thin one. [current] outlines the shot the review screen
 * shows.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun ShotStrip(
    shots: List<Shot>,
    selectedId: String?,
    onTap: (index: Int, shot: Shot) -> Unit,
    onLongPress: (Shot) -> Unit,
    modifier: Modifier = Modifier,
    current: Int? = null,
    thumbTag: String = "thumb",
    thumbSize: Dp = 56.dp,
) {
    val listState = rememberLazyListState()
    val haptics = LocalHapticFeedback.current
    val selectedIndex = shots.indexOfFirst { it.id == selectedId }
    LaunchedEffect(shots.size, current) {
        val target = current ?: (shots.size - 1)
        if (target in shots.indices) listState.animateScrollToItem(target)
    }
    LazyRow(
        state = listState,
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        itemsIndexed(shots, key = { _, s -> s.id }) { index, shot ->
            val shape = RoundedCornerShape(6.dp)
            val inRun = selectedIndex >= 0 && index > selectedIndex
            val border = when {
                index == selectedIndex -> Modifier.border(4.dp, Accent, shape)
                inRun -> Modifier.border(2.dp, Accent.copy(alpha = 0.7f), shape)
                index == current -> Modifier.border(2.dp, Color.White, shape)
                else -> Modifier.border(1.dp, Color.White.copy(alpha = 0.6f), shape)
            }
            Box(
                Modifier
                    .size(thumbSize)
                    .clip(shape)
                    .then(border)
                    .combinedClickable(
                        onClick = { onTap(index, shot) },
                        onLongClick = {
                            haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                            onLongPress(shot)
                        },
                    )
                    .semantics {
                        contentDescription = "Shot ${index + 1}"
                        selected = index == selectedIndex
                    }
                    .testTag(thumbTag),
            ) {
                ShotThumbnail(shot, Modifier.matchParentSize().padding(if (index == selectedIndex) 4.dp else 1.dp))
                if (index == selectedIndex) {
                    Icon(
                        Icons.Default.CheckCircle,
                        contentDescription = null,
                        tint = Accent,
                        modifier = Modifier.align(Alignment.TopEnd).padding(2.dp).size(16.dp).clip(CircleShape),
                    )
                }
            }
        }
    }
}

/**
 * The four actions on the selected shot and the photos after it (shot-actions
 * Requirement 2), with confirmations for the two deletions. An action that
 * cannot be done is shown disabled with its reason.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ShotActionsSheet(menu: ShotActionsMenu, onAction: (ShotAction) -> Unit, onDismiss: () -> Unit) {
    var confirming by remember(menu) { mutableStateOf<ShotAction?>(null) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    if (confirming == null) {
        ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
            Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(bottom = 16.dp).testTag("shotActions")) {
                Text(
                    menu.title,
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(horizontal = 24.dp, vertical = 8.dp).testTag("shotActionsTitle"),
                )
                HorizontalDivider()
                ActionRow("Delete this photo", null, "actionDeleteShot") { confirming = ShotAction.DELETE_SHOT }
                ActionRow(menu.deleteRunLabel, menu.deleteRunReason, "actionDeleteRun") { confirming = ShotAction.DELETE_RUN }
                ActionRow(menu.moveLabel, menu.moveReason, "actionMove") { onAction(ShotAction.MOVE_TO_NEXT_PAGE) }
                ActionRow(menu.newPageLabel, menu.newPageReason, "actionNewPage") { onAction(ShotAction.NEW_PAGE) }
            }
        }
    }
    when (val action = confirming) {
        ShotAction.DELETE_SHOT, ShotAction.DELETE_RUN -> {
            val run = action == ShotAction.DELETE_RUN
            val p = menu.pageNumber
            val removesPage = if (run) menu.deleteRunRemovesPage else menu.deleteShotRemovesPage
            AlertDialog(
                onDismissRequest = onDismiss,
                title = {
                    Text(if (run) "Delete ${ShotActionsMenu.photos(menu.runSize)} from page $p?" else "Delete this photo from page $p?")
                },
                text = {
                    Text(
                        when {
                            removesPage && run -> "They are all of page $p's photos, so the page is deleted too and later pages move up by one."
                            removesPage -> "It is the only photo of page $p, so the page is deleted too and later pages move up by one."
                            run -> "The photos are deleted from this phone and from the server."
                            else -> "The photo is deleted from this phone and from the server."
                        }
                    )
                },
                confirmButton = {
                    TextButton(onClick = { onAction(action) }, modifier = Modifier.testTag("confirmShotAction")) { Text("Delete") }
                },
                dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
            )
        }
        else -> Unit
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ActionRow(label: String, reason: String?, tag: String, onClick: () -> Unit) {
    val enabled = reason == null
    Column(
        Modifier
            .fillMaxWidth()
            .combinedClickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 24.dp, vertical = 12.dp)
            .testTag(tag),
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodyLarge,
            color = if (enabled) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
        )
        if (reason != null) {
            Text(
                reason,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("${tag}Reason"),
            )
        }
    }
}

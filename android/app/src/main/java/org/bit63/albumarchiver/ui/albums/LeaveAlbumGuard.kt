package org.bit63.albumarchiver.ui.albums

import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.PageFeedback

/**
 * What is left to do on an album before it goes back on the shelf
 * (single-shot glare spec, Requirement 4): its red-dot pages, and its pages
 * whose feedback has not arrived yet.
 */
data class AlbumWorkLeft(val warned: Int = 0, val pending: Int = 0, val serverConfigured: Boolean = false) {
    /** Whether leaving asks first: never without a server, nor when every page has feedback without a warning (Requirement 4.4). */
    val ask: Boolean get() = serverConfigured && (warned > 0 || pending > 0)

    val text: String
        get() = listOfNotNull(
            if (warned > 0) (if (warned == 1) "1 page needs another shot" else "$warned pages need another shot") else null,
            if (pending > 0) (if (pending == 1) "1 page is still being checked" else "$pending pages are still being checked") else null,
        ).joinToString(" and ", postfix = ".")
}

/** Server page states before a run's feedback is in. */
private val UNFINISHED = setOf("changed", "ready", "processing")

/**
 * The album's work left. A page with shots is pending while a shot is queued
 * for upload ([queued]), or its feedback has not come back from a run since
 * (none yet, or the server has not finished with it); a pending page is not
 * also counted as warned, since its warnings are about to be replaced.
 */
fun albumWorkLeft(pages: List<Page>, feedback: List<PageFeedback>, queued: Collection<String>, warned: Set<String>, serverConfigured: Boolean): AlbumWorkLeft {
    val byPage = feedback.associateBy { it.pageId }
    val queuedSet = queued.toSet()
    val withShots = pages.filter { it.shotCount > 0 }
    val pending = withShots.filter { p ->
        val fb = byPage[p.id]
        p.id in queuedSet || fb == null || fb.state in UNFINISHED
    }.map { it.id }.toSet()
    val warnedLeft = withShots.count { it.id in warned && it.id !in pending }
    return AlbumWorkLeft(warnedLeft, pending.size, serverConfigured)
}

/**
 * Asks once before each way out of an album (switching album, a new album,
 * the server album list) while it has work left (Requirement 4.2). It never
 * blocks: "Leave anyway" does what the user asked for.
 */
class LeaveAlbumGuard {
    /** The way out the question is about; null while no question is open. */
    var leaving: (() -> Unit)? by mutableStateOf(null)
        private set

    /** Leaves at once when [work] says there is nothing to ask about, else opens the question. */
    fun request(work: AlbumWorkLeft, leave: () -> Unit) {
        if (work.ask) leaving = leave else leave()
    }

    fun leaveAnyway() {
        val leave = leaving
        leaving = null
        leave?.invoke()
    }

    fun stay(openOverview: () -> Unit) {
        leaving = null
        openOverview()
    }

    fun dismiss() {
        leaving = null
    }
}

/** The question, with the numbers kept current while it is open (Requirement 4.5): the page feedback keeps being polled. */
@Composable
fun LeaveAlbumDialog(albumName: String, work: AlbumWorkLeft, onStay: () -> Unit, onLeave: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Leave $albumName?") },
        text = { Text(if (work.ask) work.text else "Every page has its feedback now.", modifier = Modifier.testTag("leaveWorkLeft")) },
        confirmButton = { TextButton(onClick = onStay, modifier = Modifier.testTag("leaveStay")) { Text("Stay") } },
        dismissButton = { TextButton(onClick = onLeave, modifier = Modifier.testTag("leaveAnyway")) { Text("Leave anyway") } },
    )
}

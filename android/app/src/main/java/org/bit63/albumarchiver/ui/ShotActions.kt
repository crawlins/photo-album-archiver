package org.bit63.albumarchiver.ui

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.Limits
import org.bit63.albumarchiver.data.MoveResult
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.Shot
import kotlin.coroutines.CoroutineContext

enum class ShotAction { DELETE_SHOT, DELETE_RUN, MOVE_TO_NEXT_PAGE, NEW_PAGE }

/**
 * What the shot actions menu offers for the selected shot (shot-actions
 * Requirement 2). A reason is set for each action that cannot be done; the
 * menu shows it disabled with the reason rather than hiding it.
 */
data class ShotActionsMenu(
    val shot: Shot,
    val pageNumber: Int,
    /** 1-based place of the shot on its page. */
    val position: Int,
    val pageShots: Int,
    /** Shots after it on the page. */
    val after: Int,
    /** The page is the album's last, so the move goes to a new page. */
    val toNewPage: Boolean,
    val deleteShotRemovesPage: Boolean,
    val deleteRunRemovesPage: Boolean,
    val deleteRunReason: String?,
    val moveReason: String?,
    val newPageReason: String?,
) {
    val runSize: Int get() = after + 1
    val title: String get() = "Page $pageNumber, photo $position of $pageShots"
    private val subject: String get() = if (after == 0) "this photo" else "this photo and the $after after it"
    val deleteRunLabel: String get() = if (after == 0) "Delete this photo and the ones after it" else "Delete $subject"
    val moveLabel: String get() = "Move $subject to ${if (toNewPage) "a new page" else "page"} ${pageNumber + 1}"
    val newPageLabel: String get() = "Create a new page with $subject"

    fun reason(action: ShotAction): String? = when (action) {
        ShotAction.DELETE_SHOT -> null
        ShotAction.DELETE_RUN -> deleteRunReason
        ShotAction.MOVE_TO_NEXT_PAGE -> moveReason
        ShotAction.NEW_PAGE -> newPageReason
    }

    companion object {
        const val NO_PHOTOS_AFTER = "No photos after this one"
        const val WHOLE_LAST_PAGE = "Already the whole last page"
        const val WHOLE_PAGE = "Already the whole page"
        const val ALBUM_FULL = "Album is full (500 pages)"
        fun pageFull(number: Int) = "Page $number would have more than 25 photos"

        /** The menu for [run] (the selected shot and the shots after it) on [page] of an album with [pages]. */
        fun of(run: List<Shot>, page: Page, pages: List<Page>): ShotActionsMenu {
            val isLast = pages.lastOrNull()?.id == page.id
            val next = pages.firstOrNull { it.position == page.position + 1 }
            val whole = run.size >= page.shotCount
            val full = pages.size >= Limits.MAX_PAGES_PER_ALBUM
            return ShotActionsMenu(
                shot = run.first(),
                pageNumber = page.position,
                position = (page.shotCount - run.size + 1).coerceAtLeast(1),
                pageShots = page.shotCount,
                after = run.size - 1,
                toNewPage = isLast,
                deleteShotRemovesPage = page.shotCount <= 1 && !isLast,
                deleteRunRemovesPage = whole && !isLast,
                deleteRunReason = if (run.size == 1) NO_PHOTOS_AFTER else null,
                moveReason = when {
                    isLast && whole -> WHOLE_LAST_PAGE
                    isLast && full -> ALBUM_FULL
                    next != null && next.shotCount + run.size > Limits.MAX_SHOTS_PER_PAGE -> pageFull(next.position)
                    else -> null
                },
                newPageReason = when {
                    whole -> WHOLE_PAGE
                    full -> ALBUM_FULL
                    else -> null
                },
            )
        }

        fun photos(n: Int) = if (n == 1) "1 photo" else "$n photos"
    }
}

/** What an action did. */
sealed interface ShotActionOutcome {
    data class Deleted(val count: Int) : ShotActionOutcome
    data class Moved(val result: MoveResult.Moved, val message: String) : ShotActionOutcome
    /** Nothing changed; [message] says why. */
    data class Refused(val message: String) : ShotActionOutcome
}

/**
 * Selection and the shot actions menu for one thumbnail strip, shared by the
 * capture and review screens (shot-actions Requirements 1 and 2). A long
 * press selects a shot; a long press on the selected shot opens the menu.
 * The selection is plain view-model state, so it does not outlive the screen.
 */
class ShotActions(
    private val repo: AlbumRepository,
    private val scope: CoroutineScope,
    private val io: CoroutineContext,
) {
    private val _selected = MutableStateFlow<String?>(null)
    val selected: StateFlow<String?> = _selected

    private val _menu = MutableStateFlow<ShotActionsMenu?>(null)
    val menu: StateFlow<ShotActionsMenu?> = _menu

    /** A tap on a thumbnail; returns false when nothing is selected, so the tap does what it normally does. */
    fun onTap(shotId: String): Boolean {
        val current = _selected.value ?: return false
        _selected.value = if (current == shotId) null else shotId
        return true
    }

    fun onLongPress(shotId: String) {
        if (_selected.value == shotId) openMenu(shotId) else _selected.value = shotId
    }

    fun clear() {
        _selected.value = null
        _menu.value = null
    }

    /** Closes the menu and keeps the selection (Requirement 2.5). */
    fun closeMenu() {
        _menu.value = null
    }

    private fun openMenu(shotId: String) {
        scope.launch {
            val menu = withContext(io) { build(shotId) }
            if (menu == null) clear() else if (_selected.value == shotId) _menu.value = menu
        }
    }

    private suspend fun build(shotId: String): ShotActionsMenu? {
        val run = repo.runOf(shotId)
        if (run.isEmpty()) return null
        val page = repo.page(run.first().pageId) ?: return null
        return ShotActionsMenu.of(run, page, repo.pages(page.albumId))
    }

    /** Clears the selection and carries out [action] on the shot [menu] was opened for. */
    suspend fun perform(menu: ShotActionsMenu, action: ShotAction): ShotActionOutcome {
        clear()
        val id = menu.shot.id
        return withContext(io) {
            when (action) {
                ShotAction.DELETE_SHOT -> repo.deleteShot(id)?.let { ShotActionOutcome.Deleted(1) } ?: gone()
                ShotAction.DELETE_RUN -> repo.deleteRun(id)?.let { ShotActionOutcome.Deleted(it.count) } ?: gone()
                ShotAction.MOVE_TO_NEXT_PAGE -> outcome(repo.moveRunToNextPage(id), menu, newPage = false)
                ShotAction.NEW_PAGE -> outcome(repo.splitRunToNewPage(id), menu, newPage = true)
            }
        }
    }

    private fun gone() = ShotActionOutcome.Refused("That photo is no longer there")

    private fun outcome(r: MoveResult, menu: ShotActionsMenu, newPage: Boolean): ShotActionOutcome = when (r) {
        is MoveResult.Moved -> ShotActionOutcome.Moved(
            r,
            if (newPage) "Made page ${r.page.position} from ${ShotActionsMenu.photos(r.count)}"
            else "Moved ${ShotActionsMenu.photos(r.count)} to page ${r.page.position}",
        )
        MoveResult.PageFull -> ShotActionOutcome.Refused(ShotActionsMenu.pageFull(menu.pageNumber + 1))
        MoveResult.AlbumFull -> ShotActionOutcome.Refused(ShotActionsMenu.ALBUM_FULL)
        MoveResult.NothingToMove -> ShotActionOutcome.Refused(if (newPage) ShotActionsMenu.WHOLE_PAGE else ShotActionsMenu.WHOLE_LAST_PAGE)
        MoveResult.NotFound -> gone()
    }
}

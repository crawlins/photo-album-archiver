package org.bit63.albumarchiver.ui.review

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.Shot
import org.bit63.albumarchiver.data.ShotState
import org.bit63.albumarchiver.ui.ShotAction
import org.bit63.albumarchiver.ui.ShotActionOutcome
import org.bit63.albumarchiver.ui.ShotActions
import org.bit63.albumarchiver.upload.PageLoader
import org.bit63.albumarchiver.upload.ShotFetcher
import kotlin.coroutines.CoroutineContext

/** One place in the album: a page and a shot index on it. */
data class Slot(val pageId: String, val index: Int)

/** Every shot position in the album in order; a page not yet loaded counts its shots from the metadata. */
fun slots(pages: List<Page>): List<Slot> =
    pages.sortedBy { it.position }.flatMap { p -> (0 until p.shotCount).map { Slot(p.id, it) } }

/**
 * Where the review screen goes after a deletion (Requirement 12.6): the shot
 * that now sits where the deleted one was (the next shot), else the one just
 * before it, else nowhere, meaning back to the page overview.
 */
fun landingAfterDeletion(slotsAfter: List<Slot>, deletedAt: Int): Slot? = when {
    deletedAt < slotsAfter.size -> slotsAfter[deletedAt]
    slotsAfter.isNotEmpty() -> slotsAfter.last()
    else -> null
}

/** Loading state of a page's shot list or a shot's file, for placeholders (Requirements 14.5, 14.9). */
enum class LoadState { LOADING, FAILED }

/** Loads pages and shots of server albums as they are shown; shared by the overview and the review screen. */
class OnDemandLoader(
    private val pageLoader: PageLoader,
    private val shotFetcher: ShotFetcher,
    private val scope: kotlinx.coroutines.CoroutineScope,
) {
    private val _pages = MutableStateFlow<Map<String, LoadState>>(emptyMap())
    val pages: StateFlow<Map<String, LoadState>> = _pages
    private val _shots = MutableStateFlow<Map<String, LoadState>>(emptyMap())
    val shots: StateFlow<Map<String, LoadState>> = _shots

    /** Fetches a page's shot list unless it is loaded or loading; a failed load is retried on the next call. */
    fun ensurePage(page: Page) {
        if (page.shotsLoaded || _pages.value[page.id] == LoadState.LOADING) return
        _pages.update { it + (page.id to LoadState.LOADING) }
        scope.launch {
            val ok = pageLoader.load(page.albumId, page.id)
            _pages.update { if (ok) it - page.id else it + (page.id to LoadState.FAILED) }
        }
    }

    /** Fetches a full shot unless it is present or loading. */
    fun ensureShot(shot: Shot) {
        if (shot.state == ShotState.PRESENT || _shots.value[shot.id] == LoadState.LOADING) return
        _shots.update { it + (shot.id to LoadState.LOADING) }
        scope.launch {
            val r = shotFetcher.fetch(shot)
            _shots.update { if (r == ShotFetcher.Result.FAILED) it + (shot.id to LoadState.FAILED) else it - shot.id }
        }
    }
}

/** The page overview (Requirement 11.6). */
@OptIn(ExperimentalCoroutinesApi::class)
class PageOverviewViewModel(
    private val repo: AlbumRepository,
    pageLoader: PageLoader,
    shotFetcher: ShotFetcher,
    val albumId: String,
    private val io: CoroutineContext = Dispatchers.IO,
) : ViewModel() {
    val loader = OnDemandLoader(pageLoader, shotFetcher, viewModelScope)

    val albumName: StateFlow<String> = repo.observeAlbum(albumId).map { it?.name ?: "" }
        .stateIn(viewModelScope, SharingStarted.Eagerly, "")

    data class Cell(val page: Page, val firstShot: Shot?)

    val cells: StateFlow<List<Cell>> = combine(repo.observePages(albumId), repo.observeAlbumShots(albumId)) { pages, shots ->
        val first = shots.groupBy { it.pageId }.mapValues { it.value.first() }
        pages.map { Cell(it, first[it.id]) }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    fun deletePage(pageId: String) {
        viewModelScope.launch { withContext(io) { repo.deletePage(pageId) } }
    }
}

/**
 * The review screen (Requirements 11 and 12): one page's shots in a pager,
 * moving between pages with arrows, and deleting shots and pages.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ReviewViewModel(
    private val repo: AlbumRepository,
    pageLoader: PageLoader,
    shotFetcher: ShotFetcher,
    val albumId: String,
    startPageId: String,
    startIndex: Int,
    private val io: CoroutineContext = Dispatchers.IO,
) : ViewModel() {
    val loader = OnDemandLoader(pageLoader, shotFetcher, viewModelScope)

    /** The page and shot to show; changes with the arrows and after deletions. */
    private val _position = MutableStateFlow<Slot?>(Slot(startPageId, startIndex))
    val position: StateFlow<Slot?> = _position

    val pages: StateFlow<List<Page>> = repo.observePages(albumId)
        .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    val page: StateFlow<Page?> = combine(pages, _position) { ps, pos -> ps.firstOrNull { it.id == pos?.pageId } }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    val shots: StateFlow<List<Shot>> = _position.map { it?.pageId }
        .flatMapLatest { id -> if (id == null) flowOf(emptyList()) else repo.observeShots(id) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    /** Selection and the actions menu on the page's thumbnail strip (shot-actions spec). */
    val shotActions = ShotActions(repo, viewModelScope, io)

    private val _messages = Channel<String>(Channel.BUFFERED)
    /** Snackbar text after a shot action. */
    val messages: Flow<String> = _messages.receiveAsFlow()

    fun previousPageId(): String? = adjacent(-1)
    fun nextPageId(): String? = adjacent(+1)

    private fun adjacent(delta: Int): String? {
        val ps = pages.value
        val i = ps.indexOfFirst { it.id == _position.value?.pageId }
        if (i < 0) return null
        return ps.getOrNull(i + delta)?.id
    }

    /** Shows the first shot of another page (Requirement 11.4). */
    fun showPage(pageId: String) {
        shotActions.clear()
        _position.value = Slot(pageId, 0)
    }

    /** Shows the shot at [index] of the current page, from a tap on the strip. */
    fun showShot(index: Int) {
        _position.update { it?.copy(index = index) }
    }

    /**
     * Carries out a shot action. A deletion lands as any other deletion does
     * (Requirement 12.6); a move shows the first moved shot where it now is
     * (shot-actions Requirement 8.3).
     */
    fun shotAction(action: ShotAction) {
        val menu = shotActions.menu.value ?: return
        viewModelScope.launch {
            val at = withContext(io) {
                val index = repo.shots(menu.shot.pageId).indexOfFirst { it.id == menu.shot.id }
                slots(repo.pages(albumId)).indexOf(Slot(menu.shot.pageId, index))
            }
            when (val outcome = shotActions.perform(menu, action)) {
                is ShotActionOutcome.Deleted -> land(at)
                is ShotActionOutcome.Moved -> {
                    val target = outcome.result.page.id
                    val index = withContext(io) { repo.shots(target).indexOfFirst { it.id == outcome.result.firstShotId } }
                    _position.value = Slot(target, index.coerceAtLeast(0))
                    _messages.send(outcome.message)
                }
                is ShotActionOutcome.Refused -> _messages.send(outcome.message)
            }
        }
    }

    /** Records the shot the pager settled on, so a deletion knows where it happened. */
    fun onShotShown(index: Int) {
        _position.update { it?.copy(index = index) }
    }

    /** True when deleting [shot] also deletes its page (Requirement 12.4): its only shot, on an inner page. */
    fun deletingShotDeletesPage(page: Page): Boolean =
        page.shotCount <= 1 && pages.value.lastOrNull()?.id != page.id

    fun deleteShot(shot: Shot) {
        viewModelScope.launch {
            val (before, index) = withContext(io) {
                slots(repo.pages(albumId)) to repo.shots(shot.pageId).indexOfFirst { it.id == shot.id }
            }
            val at = before.indexOf(Slot(shot.pageId, index))
            withContext(io) { repo.deleteShot(shot.id) }
            land(at)
        }
    }

    fun deletePage(page: Page) {
        viewModelScope.launch {
            val at = withContext(io) { slots(repo.pages(albumId)) }.indexOfFirst { it.pageId == page.id }
            withContext(io) { repo.deletePage(page.id) }
            land(at)
        }
    }

    private suspend fun land(deletedAt: Int) {
        val after = withContext(io) { repo.pages(albumId) }
        _position.value = if (deletedAt < 0) null else landingAfterDeletion(slots(after), deletedAt)
        // Wait until the page list reflects the deletion before the screen redraws.
        pages.first { ps -> ps.map { it.id } == after.map { it.id } }
    }

}

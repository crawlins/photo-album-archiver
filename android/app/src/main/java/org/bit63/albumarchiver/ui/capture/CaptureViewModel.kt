package org.bit63.albumarchiver.ui.capture

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
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withContext
import org.bit63.albumarchiver.camera.ShotCamera
import org.bit63.albumarchiver.data.AddShotResult
import org.bit63.albumarchiver.data.Album
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.Limits
import org.bit63.albumarchiver.data.NextPageResult
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.SettingsStore
import org.bit63.albumarchiver.data.Shot
import org.bit63.albumarchiver.data.ShotStore
import org.bit63.albumarchiver.data.reviewIndex
import org.bit63.albumarchiver.ui.ShotAction
import org.bit63.albumarchiver.ui.ShotActionOutcome
import org.bit63.albumarchiver.ui.ShotActions
import org.bit63.albumarchiver.ui.albums.AlbumWorkLeft
import org.bit63.albumarchiver.ui.albums.albumWorkLeft
import org.bit63.albumarchiver.upload.FeedbackPoller
import java.util.UUID
import kotlin.coroutines.CoroutineContext

data class CaptureState(
    /** False until the last album has been looked up, so the empty state does not flash at launch. */
    val loaded: Boolean = false,
    val album: Album? = null,
    /** The page the next shot goes to: the album's last page, or an earlier target page (overlap coverage Requirement 4.7); null before the first shot. */
    val page: Page? = null,
    /** The album's last page, which [page] usually is. */
    val lastPage: Page? = null,
    val shots: List<Shot> = emptyList(),
    /** True when [page] is marked with a red dot (its latest feedback has a warning). */
    val warned: Boolean = false,
    val pendingUploads: Int = 0,
    val serverConfigured: Boolean = true,
    val authRejected: Boolean = false,
) {
    val pageNumber: Int get() = page?.position ?: 1
    val shotCount: Int get() = page?.shotCount ?: 0
    val pageFull: Boolean get() = shotCount >= Limits.MAX_SHOTS_PER_PAGE
    val albumFull: Boolean get() = (lastPage?.position ?: 0) >= Limits.MAX_PAGES_PER_ALBUM
    /** False while shots go to an earlier page; "Next page" then reads "Back to page M". */
    val targetIsLast: Boolean get() = page == null || lastPage == null || page.id == lastPage.id
}

sealed interface CaptureEvent {
    data class ShotSaved(val shot: Shot) : CaptureEvent
    data class PageStarted(val pageId: String, val number: Int) : CaptureEvent
    data class Message(val text: String) : CaptureEvent
    /** A page the user has left now needs another shot: vibrate once (single-shot glare Requirement 3.3). */
    data object OtherPageWarned : CaptureEvent
}

/**
 * The capture screen's logic. [takeShot] and [nextPage] are used by both the
 * on-screen buttons and the volume keys, so the limits apply to both
 * (Requirements 6.1, 6.2).
 */
@OptIn(ExperimentalCoroutinesApi::class)
class CaptureViewModel(
    private val repo: AlbumRepository,
    private val settings: SettingsStore,
    private val store: ShotStore,
    val camera: ShotCamera,
    private val io: CoroutineContext = Dispatchers.IO,
    feedback: FeedbackPoller? = null,
    uploadRunning: Flow<Boolean> = flowOf(false),
) : ViewModel() {

    private val album: Flow<Album?> = settings.lastAlbumId.flatMapLatest { id ->
        if (id == null) flowOf(null) else repo.observeAlbum(id)
    }

    /**
     * The target page and the last page, from one read of the album's pages,
     * so the two never disagree for a moment between two emissions (which
     * would make "Next page" read as a return to the last page).
     */
    private val targetAndLast: Flow<Pair<Page?, Page?>> = album.flatMapLatest { a ->
        if (a == null) flowOf(null to null) else combine(repo.observeAlbum(a.id), repo.observePages(a.id)) { al, ps ->
            val last = ps.lastOrNull()
            (ps.firstOrNull { it.id == al?.targetPageId } ?: last) to last
        }
    }

    private val page: Flow<Page?> = targetAndLast.map { it.first }.distinctUntilChanged { a, b -> a?.id == b?.id && a?.shotCount == b?.shotCount }

    /** The album's pages marked with a red dot (overlap coverage Requirement 4.3). */
    val warnedPages: StateFlow<Set<String>> = album.flatMapLatest { a ->
        if (a == null) flowOf(emptySet()) else repo.observeWarnedPages(a.id)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, emptySet())

    /**
     * Pages other than the target page that need another shot, lowest number
     * first (single-shot glare spec, Requirement 3); none while no server is
     * configured.
     */
    private fun otherWarnedOf(albumId: String): Flow<List<Page>> = combine(
        combine(repo.observePages(albumId), repo.observeFeedback(albumId)) { ps, fb -> ps to fb },
        repo.observeWarnedPages(albumId),
        repo.observeTargetPage(albumId).map { it?.id },
        settings.server,
    ) { (ps, fb), warned, target, server -> if (server == null) emptyList() else otherWarnedPages(ps, fb, warned, target) }

    /** The notice for pages the user has left (Requirement 3.1); null when there is none. */
    val otherWarned: StateFlow<OtherWarned?> = album.map { it?.id }.distinctUntilChanged().flatMapLatest { id ->
        if (id == null) flowOf(null) else otherWarnedOf(id).map { it.toNotice() }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** What is left to do on the current album, for the question before leaving it (Requirement 4). */
    val workLeft: StateFlow<AlbumWorkLeft> = album.map { it?.id }.distinctUntilChanged().flatMapLatest { id ->
        if (id == null) flowOf(AlbumWorkLeft()) else combine(
            combine(repo.observePages(id), repo.observeFeedback(id)) { ps, fb -> ps to fb },
            repo.observePendingPages(id),
            repo.observeWarnedPages(id),
            settings.server,
        ) { (ps, fb), queued, warned, server -> albumWorkLeft(ps, fb, queued, warned, server != null) }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, AlbumWorkLeft())

    private val shots: Flow<List<Shot>> = page.flatMapLatest { p ->
        if (p == null) flowOf(emptyList()) else repo.observeShots(p.id)
    }

    private val pending: Flow<Int> = album.flatMapLatest { a ->
        if (a == null) flowOf(0) else repo.observePendingShots(a.id)
    }

    val state: StateFlow<CaptureState> = combine(
        combine(album, targetAndLast, warnedPages) { a, (p, last), warned -> Quad(a, p, last, p != null && p.id in warned) },
        shots, pending,
        combine(settings.server, settings.authRejected) { s, rejected -> (s != null) to rejected },
    ) { (a, p, last, warned), s, n, (configured, rejected) ->
        CaptureState(
            loaded = true, album = a, page = p, lastPage = last, shots = s, warned = warned, pendingUploads = n,
            serverConfigured = configured, authRejected = rejected,
        )
    }.stateIn(viewModelScope, SharingStarted.Eagerly, CaptureState())

    private data class Quad(val album: Album?, val page: Page?, val last: Page?, val warned: Boolean)

    private val _flashOn = MutableStateFlow(false)
    /** Off by default and not remembered across restarts (Requirement 4.4). */
    val flashOn: StateFlow<Boolean> = _flashOn

    private val _lowStorage = MutableStateFlow(false)
    val lowStorage: StateFlow<Boolean> = _lowStorage

    private val _capturing = MutableStateFlow(false)
    val capturing: StateFlow<Boolean> = _capturing

    private val eventChannel = Channel<CaptureEvent>(Channel.BUFFERED)
    val events: Flow<CaptureEvent> = eventChannel.receiveAsFlow()

    /** Serialises captures: a press while one is in flight is dropped, not queued (Requirement 4.6). */
    private val captureLock = Mutex()

    /** Selection and the actions menu on the thumbnail strip (shot-actions spec). */
    val shotActions = ShotActions(repo, viewModelScope, io)

    init {
        refreshStorage()
        // Page feedback from the server while this screen lives (Requirement 4.1).
        feedback?.start(viewModelScope, album.map { it?.id }, uploadRunning)
        // One vibration for each page that becomes an other warned page while
        // this screen is open; what was warned when the album opened is not news.
        viewModelScope.launch {
            album.map { it?.id }.distinctUntilChanged().flatMapLatest { id ->
                if (id == null) flowOf(emptySet()) else {
                    val tracker = NewlyWarned()
                    otherWarnedOf(id).map { ps -> ps.map { it.id }.toSet() }.distinctUntilChanged().map { tracker.update(it) }
                }
            }.collect { if (it.isNotEmpty()) eventChannel.send(CaptureEvent.OtherPageWarned) }
        }
        // The strip now shows another page, so a selection on it no longer applies.
        // Only a change from one page to another counts: the state starts with
        // no page, and its first page arriving after a long press would
        // otherwise clear that press's selection and menu.
        viewModelScope.launch {
            state.map { it.page?.id }.filterNotNull().distinctUntilChanged().drop(1).collect { shotActions.clear() }
        }
    }

    /** Carries out a shot action; the capture screen keeps showing the current page, wherever that now is. */
    fun shotAction(action: ShotAction) {
        val menu = shotActions.menu.value ?: return
        viewModelScope.launch {
            when (val outcome = shotActions.perform(menu, action)) {
                is ShotActionOutcome.Moved -> message(outcome.message)
                is ShotActionOutcome.Refused -> message(outcome.message)
                is ShotActionOutcome.Deleted -> Unit
            }
        }
    }

    fun refreshStorage() {
        viewModelScope.launch { _lowStorage.value = withContext(io) { store.isLowOnSpace() } }
    }

    fun toggleFlash() {
        _flashOn.value = !_flashOn.value
        camera.setFlash(_flashOn.value)
    }

    fun takeShot() {
        shotActions.clear()
        if (!captureLock.tryLock()) return
        viewModelScope.launch {
            try {
                _capturing.value = true
                capture()
            } finally {
                _capturing.value = false
                captureLock.unlock()
            }
        }
    }

    private suspend fun capture() {
        val current = state.value
        val album = current.album ?: return
        if (current.pageFull) {
            message(PAGE_FULL)
            return
        }
        val shotId = UUID.randomUUID().toString()
        val temp = store.newTempFile(shotId)
        val captured = camera.capture(temp)
        if (captured.isFailure) {
            withContext(io) { temp.delete() }
            message("Couldn't take the shot: ${captured.exceptionOrNull()?.message ?: "camera error"}")
            return
        }
        val target = current.page?.id?.takeIf { !current.targetIsLast }
        val result = try {
            withContext(io) { repo.addShot(album.id, shotId, store.finish(temp), target) }
        } catch (e: Exception) {
            withContext(io) { temp.delete() }
            message("Couldn't save the shot: ${e.message ?: "storage error"}")
            return
        }
        when (result) {
            is AddShotResult.Added -> {
                eventChannel.send(CaptureEvent.ShotSaved(result.shot))
                refreshStorage()
            }
            AddShotResult.PageFull -> {
                withContext(io) { temp.delete() }
                message(PAGE_FULL)
            }
            AddShotResult.NoAlbum -> withContext(io) { temp.delete() }
        }
    }

    /** Starts the next page; from an earlier target page it only returns to the last page (Requirement 4.8). */
    fun nextPage() {
        shotActions.clear()
        val album = state.value.album ?: return
        if (!state.value.targetIsLast) {
            backToLastPage()
            return
        }
        viewModelScope.launch {
            when (val r = withContext(io) { repo.startNextPage(album.id) }) {
                is NextPageResult.Started -> eventChannel.send(CaptureEvent.PageStarted(r.page.id, r.page.position))
                NextPageResult.CurrentPageEmpty -> message("This page has no shots yet")
                NextPageResult.AlbumFull -> message(ALBUM_FULL)
                NextPageResult.NoAlbum -> Unit
            }
        }
    }

    /** Makes [pageId] the page new shots go to (Requirement 4.7). */
    fun addShotsToPage(pageId: String) {
        shotActions.clear()
        val album = state.value.album ?: return
        viewModelScope.launch { withContext(io) { repo.setTargetPage(album.id, pageId) } }
    }

    /** Where a tap on the notice opens the review of [pageId]: its first photo with a box for one of its warnings, else its first (Requirement 3.2). */
    suspend fun reviewIndexFor(pageId: String): Int = withContext(io) {
        val ids = repo.shots(pageId).map { it.id }
        (repo.observePageFeedback(pageId).first()?.warnings ?: emptyList()).reviewIndex(ids)
    }

    /** Back to the album's last page as the target, without creating a page (Requirement 4.8). */
    fun backToLastPage() {
        shotActions.clear()
        val album = state.value.album ?: return
        viewModelScope.launch { withContext(io) { repo.setTargetPage(album.id, null) } }
    }

    fun undoNextPage(pageId: String) {
        viewModelScope.launch {
            if (!withContext(io) { repo.undoNextPage(pageId) }) message("The new page already has shots")
        }
    }

    /** The camera could not be opened (Requirement 4.7 and the design's error table). */
    fun cameraUnavailable(error: Throwable) {
        viewModelScope.launch { message("Camera unavailable: ${error.message ?: "unknown error"}") }
    }

    private suspend fun message(text: String) = eventChannel.send(CaptureEvent.Message(text))

    companion object {
        const val PAGE_FULL = "Page full: 25 shots. Press Next page to move on."
        const val ALBUM_FULL = "Album full: 500 pages. Start a new album for the rest."
    }
}

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
import org.bit63.albumarchiver.ui.ShotAction
import org.bit63.albumarchiver.ui.ShotActionOutcome
import org.bit63.albumarchiver.ui.ShotActions
import java.util.UUID
import kotlin.coroutines.CoroutineContext

data class CaptureState(
    /** False until the last album has been looked up, so the empty state does not flash at launch. */
    val loaded: Boolean = false,
    val album: Album? = null,
    /** The page the next shot goes to: always the album's last page; null before the first shot. */
    val page: Page? = null,
    val shots: List<Shot> = emptyList(),
    val pendingUploads: Int = 0,
    val serverConfigured: Boolean = true,
    val authRejected: Boolean = false,
) {
    val pageNumber: Int get() = page?.position ?: 1
    val shotCount: Int get() = page?.shotCount ?: 0
    val pageFull: Boolean get() = shotCount >= Limits.MAX_SHOTS_PER_PAGE
    val albumFull: Boolean get() = (page?.position ?: 0) >= Limits.MAX_PAGES_PER_ALBUM
}

sealed interface CaptureEvent {
    data class ShotSaved(val shot: Shot) : CaptureEvent
    data class PageStarted(val pageId: String, val number: Int) : CaptureEvent
    data class Message(val text: String) : CaptureEvent
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
) : ViewModel() {

    private val album: Flow<Album?> = settings.lastAlbumId.flatMapLatest { id ->
        if (id == null) flowOf(null) else repo.observeAlbum(id)
    }

    private val page: Flow<Page?> = album.flatMapLatest { a ->
        if (a == null) flowOf(null) else repo.observeLastPage(a.id)
    }

    private val shots: Flow<List<Shot>> = page.flatMapLatest { p ->
        if (p == null) flowOf(emptyList()) else repo.observeShots(p.id)
    }

    private val pending: Flow<Int> = album.flatMapLatest { a ->
        if (a == null) flowOf(0) else repo.observePendingShots(a.id)
    }

    val state: StateFlow<CaptureState> = combine(
        album, page, shots, pending,
        combine(settings.server, settings.authRejected) { s, rejected -> (s != null) to rejected },
    ) { a, p, s, n, (configured, rejected) ->
        CaptureState(
            loaded = true, album = a, page = p, shots = s, pendingUploads = n,
            serverConfigured = configured, authRejected = rejected,
        )
    }.stateIn(viewModelScope, SharingStarted.Eagerly, CaptureState())

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
        // The strip now shows another page, so a selection on it no longer applies.
        viewModelScope.launch {
            state.map { it.page?.id }.distinctUntilChanged().drop(1).collect { shotActions.clear() }
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
        val result = try {
            withContext(io) { repo.addShot(album.id, shotId, store.finish(temp)) }
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

    fun nextPage() {
        shotActions.clear()
        val album = state.value.album ?: return
        viewModelScope.launch {
            when (val r = withContext(io) { repo.startNextPage(album.id) }) {
                is NextPageResult.Started -> eventChannel.send(CaptureEvent.PageStarted(r.page.id, r.page.position))
                NextPageResult.CurrentPageEmpty -> message("This page has no shots yet")
                NextPageResult.AlbumFull -> message(ALBUM_FULL)
                NextPageResult.NoAlbum -> Unit
            }
        }
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

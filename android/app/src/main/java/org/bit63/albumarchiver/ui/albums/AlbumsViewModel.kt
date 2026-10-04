package org.bit63.albumarchiver.ui.albums

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.bit63.albumarchiver.data.Album
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.AlbumSummary
import org.bit63.albumarchiver.data.SettingsStore
import kotlin.coroutines.CoroutineContext

/** How far an album has got to the server, as the drawer shows it (Requirement 8.7). */
enum class UploadState(val label: String) { UPLOADED("uploaded"), UPLOADING("uploading"), WAITING("waiting") }

data class AlbumRow(val summary: AlbumSummary, val upload: UploadState)

fun uploadState(albumId: String, pendingOps: Int, running: Boolean, headAlbumId: String?): UploadState = when {
    pendingOps == 0 -> UploadState.UPLOADED
    running && headAlbumId == albumId -> UploadState.UPLOADING
    else -> UploadState.WAITING
}

/** The navigation drawer: the albums on this phone, creating, editing, switching and deleting them (Requirement 2). */
class AlbumsViewModel(
    private val repo: AlbumRepository,
    private val settings: SettingsStore,
    uploadRunning: Flow<Boolean>,
    private val io: CoroutineContext = Dispatchers.IO,
) : ViewModel() {

    val albums: StateFlow<List<AlbumRow>> = combine(
        repo.observeSummaries(), uploadRunning, repo.observeQueueHeadAlbum(),
    ) { summaries, running, head ->
        summaries.map { AlbumRow(it, uploadState(it.id, it.pendingOps, running, head)) }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    /** Creates an album and makes it the current album (Requirement 2.3). */
    fun create(form: AlbumForm, onDone: (Album) -> Unit = {}) {
        val size = form.pageSize ?: return
        if (!form.isValid) return
        viewModelScope.launch {
            val album = withContext(io) { repo.createAlbum(form.name, size) }
            settings.setLastAlbumId(album.id)
            onDone(album)
        }
    }

    fun update(albumId: String, form: AlbumForm) {
        val size = form.pageSize ?: return
        if (!form.isValid) return
        viewModelScope.launch { withContext(io) { repo.updateAlbum(albumId, form.name, size) } }
    }

    fun select(albumId: String) {
        viewModelScope.launch { settings.setLastAlbumId(albumId) }
    }

    suspend fun album(albumId: String): Album? = withContext(io) { repo.album(albumId) }

    fun delete(albumId: String, alsoOnServer: Boolean) {
        viewModelScope.launch { withContext(io) { repo.deleteAlbum(albumId, alsoOnServer) } }
    }
}

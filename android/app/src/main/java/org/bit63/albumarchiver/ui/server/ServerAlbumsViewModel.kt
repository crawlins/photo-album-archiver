package org.bit63.albumarchiver.ui.server

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.SettingsStore
import org.bit63.albumarchiver.upload.AlbumImporter
import org.bit63.albumarchiver.upload.ApiResult
import org.bit63.albumarchiver.upload.ServerAccess
import org.bit63.albumarchiver.upload.ServerAlbumSummary

/** Why the server albums list could not be shown (Requirement 13.4). */
enum class ServerProblem(val message: String, val toSettings: Boolean) {
    NO_SERVER("No server is set up.", true),
    UNREACHABLE("Can't reach the server.", false),
    AUTH("The server rejected the access token.", true),
    OTHER("The server returned an error.", false),
}

data class ServerAlbumRow(val album: ServerAlbumSummary, val onPhone: Boolean)

sealed interface ServerAlbumsState {
    data object Loading : ServerAlbumsState
    data class Loaded(val rows: List<ServerAlbumRow>) : ServerAlbumsState
    data class Error(val problem: ServerProblem) : ServerAlbumsState
}

/** The "Server albums" screen (Requirements 13 and 14.1). Nothing is cached: the list is always the server's current answer. */
class ServerAlbumsViewModel(
    private val server: ServerAccess,
    private val repo: AlbumRepository,
    private val settings: SettingsStore,
    private val importer: AlbumImporter,
) : ViewModel() {
    private val fetched = MutableStateFlow<FetchState>(FetchState.Loading)
    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing

    private val _message = MutableStateFlow<String?>(null)
    /** A one-off error from deleting or opening an album. */
    val message: StateFlow<String?> = _message

    private sealed interface FetchState {
        data object Loading : FetchState
        data class Done(val albums: List<ServerAlbumSummary>) : FetchState
        data class Failed(val problem: ServerProblem) : FetchState
    }

    val state: StateFlow<ServerAlbumsState> = combine(fetched, repo.observeAlbumIds()) { f, local ->
        when (f) {
            FetchState.Loading -> ServerAlbumsState.Loading
            is FetchState.Failed -> ServerAlbumsState.Error(f.problem)
            is FetchState.Done -> ServerAlbumsState.Loaded(f.albums.map { ServerAlbumRow(it, it.id in local) })
        }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, ServerAlbumsState.Loading)

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _refreshing.value = true
            if (fetched.value is FetchState.Failed) fetched.value = FetchState.Loading
            fetched.value = load()
            _refreshing.value = false
        }
    }

    private suspend fun load(): FetchState {
        val client = server.client() ?: return FetchState.Failed(ServerProblem.NO_SERVER)
        return when (val r = client.listAlbums()) {
            is ApiResult.Ok -> FetchState.Done(r.value)
            ApiResult.AuthFailed -> FetchState.Failed(ServerProblem.AUTH)
            is ApiResult.NetworkError -> FetchState.Failed(ServerProblem.UNREACHABLE)
            is ApiResult.HttpError -> FetchState.Failed(ServerProblem.OTHER)
        }
    }

    /**
     * Deletes a server-only album (Requirement 13.5). Albums also on this phone
     * are refused: they are deleted from the drawer instead (Requirement 13.6).
     */
    fun deleteFromServer(albumId: String) {
        viewModelScope.launch {
            if (repo.album(albumId) != null) return@launch
            val client = server.client() ?: run { _message.value = ServerProblem.NO_SERVER.message; return@launch }
            when (client.deleteAlbum(albumId)) {
                is ApiResult.Ok -> fetched.update { f ->
                    if (f is FetchState.Done) FetchState.Done(f.albums.filterNot { it.id == albumId }) else f
                }
                ApiResult.AuthFailed -> _message.value = "Couldn't delete: ${ServerProblem.AUTH.message}"
                is ApiResult.NetworkError -> _message.value = "Couldn't delete: ${ServerProblem.UNREACHABLE.message}"
                is ApiResult.HttpError -> _message.value = "Couldn't delete: ${ServerProblem.OTHER.message}"
            }
        }
    }

    /** Opens an album on this phone and makes it the current album (Requirement 14.1). */
    fun open(albumId: String, onOpened: () -> Unit) {
        viewModelScope.launch {
            when (val r = importer.open(albumId)) {
                AlbumImporter.Result.Opened -> {
                    settings.setLastAlbumId(albumId)
                    onOpened()
                }
                AlbumImporter.Result.NoServer -> _message.value = ServerProblem.NO_SERVER.message
                AlbumImporter.Result.AuthFailed -> _message.value = ServerProblem.AUTH.message
                AlbumImporter.Result.Unreachable -> _message.value = "Couldn't open: ${ServerProblem.UNREACHABLE.message}"
                is AlbumImporter.Result.Failed -> _message.value = "Couldn't open: the server returned ${r.code}."
            }
        }
    }

    fun clearMessage() {
        _message.value = null
    }
}

package org.bit63.albumarchiver.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import org.bit63.albumarchiver.data.ServerConfig
import org.bit63.albumarchiver.data.SettingsStore
import org.bit63.albumarchiver.upload.ApiResult
import org.bit63.albumarchiver.upload.ServerClient

enum class ConnectionResult(val message: String) {
    OK("Connected."),
    UNREACHABLE("Can't reach the server."),
    REJECTED("The server rejected the access token."),
    ERROR("The server answered with an error."),
}

data class SettingsForm(
    val url: String = "",
    val token: String = "",
    val unmeteredOnly: Boolean = true,
    val testing: Boolean = false,
    val result: ConnectionResult? = null,
    val saved: Boolean = false,
) {
    val urlError: String? get() = ServerClient.urlProblem(url)
    /** Requirement 9.4. */
    val cleartextWarning: Boolean get() = ServerClient.isCleartext(url)
    val canTest: Boolean get() = url.isNotBlank() && urlError == null && !testing
}

/** The settings screen (Requirement 9). */
class SettingsViewModel(
    private val settings: SettingsStore,
    private val onChanged: suspend () -> Unit,
    private val http: OkHttpClient = ServerClient.defaultHttpClient,
) : ViewModel() {
    private val _form = MutableStateFlow(SettingsForm())
    val form: StateFlow<SettingsForm> = _form

    init {
        viewModelScope.launch {
            _form.value = SettingsForm(
                url = settings.serverUrl.first(),
                token = settings.token.first(),
                unmeteredOnly = settings.unmeteredOnly.first(),
            )
        }
    }

    fun setUrl(url: String) = _form.update { it.copy(url = url, result = null, saved = false) }
    fun setToken(token: String) = _form.update { it.copy(token = token, result = null, saved = false) }

    fun setUnmeteredOnly(value: Boolean) {
        _form.update { it.copy(unmeteredOnly = value) }
        viewModelScope.launch {
            settings.setUnmeteredOnly(value)
            onChanged()
        }
    }

    fun save() {
        val f = _form.value
        if (f.urlError != null) return
        viewModelScope.launch {
            settings.setServer(f.url.trim(), f.token.trim())
            onChanged()
            _form.update { it.copy(saved = true) }
        }
    }

    /** Calls `GET /api/v1/ping` with the values as typed, saved or not (Requirement 9.2). */
    fun testConnection() {
        val f = _form.value
        if (!f.canTest) return
        _form.update { it.copy(testing = true, result = null) }
        viewModelScope.launch {
            val result = try {
                when (val r = ServerClient(ServerConfig(f.url.trim(), f.token.trim()), http).ping()) {
                    is ApiResult.Ok -> ConnectionResult.OK
                    ApiResult.AuthFailed -> ConnectionResult.REJECTED
                    is ApiResult.NetworkError -> ConnectionResult.UNREACHABLE
                    is ApiResult.HttpError -> ConnectionResult.ERROR
                }
            } catch (e: IllegalArgumentException) {
                ConnectionResult.UNREACHABLE
            }
            _form.update { it.copy(testing = false, result = result) }
        }
    }
}

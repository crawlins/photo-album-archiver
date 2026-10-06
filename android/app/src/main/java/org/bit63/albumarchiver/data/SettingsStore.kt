package org.bit63.albumarchiver.data

import android.content.Context
import android.content.SharedPreferences
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

/** Where to upload. [url] has no trailing slash. */
data class ServerConfig(val url: String, val token: String)

/**
 * The app's small persistent settings (Requirements 1.4, 9.1, 9.3): the last
 * album, the server, the upload network choice and whether uploads are paused
 * because the server rejected the token.
 */
interface SettingsStore {
    val lastAlbumId: Flow<String?>
    suspend fun setLastAlbumId(id: String?)

    val serverUrl: Flow<String>
    val token: Flow<String>
    suspend fun setServer(url: String, token: String)

    /** True (the default) when uploads wait for an unmetered network. */
    val unmeteredOnly: Flow<Boolean>
    suspend fun setUnmeteredOnly(value: Boolean)

    /** Set when the server answers 401 or 403; cleared when the server settings change. */
    val authRejected: Flow<Boolean>
    suspend fun setAuthRejected(value: Boolean)

    /** The configured server, or null when the URL or token is blank. */
    val server: Flow<ServerConfig?>
        get() = combine(serverUrl, token) { url, token ->
            if (url.isBlank() || token.isBlank()) null else ServerConfig(url.trimEnd('/'), token)
        }

    suspend fun currentServer(): ServerConfig? = server.first()
}

/** Holds the access token. Kept apart from DataStore so it can live in encrypted storage. */
interface TokenStore {
    val token: Flow<String>
    fun set(token: String)
}

class InMemoryTokenStore(initial: String = "") : TokenStore {
    private val state = MutableStateFlow(initial)
    override val token: Flow<String> = state.asStateFlow()
    override fun set(token: String) { state.value = token }
}

/** The token in EncryptedSharedPreferences, backed by a key in the Android keystore. */
class EncryptedTokenStore(context: Context) : TokenStore {
    @Suppress("DEPRECATION")
    private val prefs: SharedPreferences = run {
        val key = androidx.security.crypto.MasterKey.Builder(context)
            .setKeyScheme(androidx.security.crypto.MasterKey.KeyScheme.AES256_GCM)
            .build()
        androidx.security.crypto.EncryptedSharedPreferences.create(
            context,
            "secrets",
            key,
            androidx.security.crypto.EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            androidx.security.crypto.EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }
    private val state = MutableStateFlow(prefs.getString(KEY, "") ?: "")
    override val token: Flow<String> = state.asStateFlow()

    override fun set(token: String) {
        prefs.edit().putString(KEY, token).apply()
        state.value = token
    }

    private companion object { const val KEY = "token" }
}

class DataStoreSettingsStore(
    private val store: DataStore<Preferences>,
    private val tokens: TokenStore,
) : SettingsStore {
    override val lastAlbumId: Flow<String?> = store.data.map { it[LAST_ALBUM] }
    override suspend fun setLastAlbumId(id: String?) {
        store.edit { if (id == null) it.remove(LAST_ALBUM) else it[LAST_ALBUM] = id }
    }

    override val serverUrl: Flow<String> = store.data.map { it[SERVER_URL] ?: "" }
    override val token: Flow<String> = tokens.token
    override suspend fun setServer(url: String, token: String) {
        tokens.set(token)
        store.edit {
            it[SERVER_URL] = url.trim()
            it[AUTH_REJECTED] = false
        }
    }

    override val unmeteredOnly: Flow<Boolean> = store.data.map { it[UNMETERED_ONLY] ?: true }
    override suspend fun setUnmeteredOnly(value: Boolean) {
        store.edit { it[UNMETERED_ONLY] = value }
    }

    override val authRejected: Flow<Boolean> = store.data.map { it[AUTH_REJECTED] ?: false }
    override suspend fun setAuthRejected(value: Boolean) {
        store.edit { it[AUTH_REJECTED] = value }
    }

    private companion object {
        val LAST_ALBUM = stringPreferencesKey("last_album_id")
        val SERVER_URL = stringPreferencesKey("server_url")
        val UNMETERED_ONLY = booleanPreferencesKey("unmetered_only")
        val AUTH_REJECTED = booleanPreferencesKey("auth_rejected")
    }
}

/** Settings held in memory, for tests. */
class InMemorySettingsStore(
    url: String = "",
    token: String = "",
    unmeteredOnly: Boolean = true,
) : SettingsStore {
    private val last = MutableStateFlow<String?>(null)
    private val urlState = MutableStateFlow(url)
    private val tokenState = MutableStateFlow(token)
    private val unmetered = MutableStateFlow(unmeteredOnly)
    private val rejected = MutableStateFlow(false)

    override val lastAlbumId: Flow<String?> = last
    override suspend fun setLastAlbumId(id: String?) { last.value = id }
    override val serverUrl: Flow<String> = urlState
    override val token: Flow<String> = tokenState
    override suspend fun setServer(url: String, token: String) {
        urlState.value = url.trim()
        tokenState.value = token
        rejected.value = false
    }
    override val unmeteredOnly: Flow<Boolean> = unmetered
    override suspend fun setUnmeteredOnly(value: Boolean) { unmetered.value = value }
    override val authRejected: Flow<Boolean> = rejected
    override suspend fun setAuthRejected(value: Boolean) { rejected.value = value }
}

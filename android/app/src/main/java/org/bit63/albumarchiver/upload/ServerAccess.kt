package org.bit63.albumarchiver.upload

import okhttp3.OkHttpClient
import org.bit63.albumarchiver.data.SettingsStore

/** Builds a [ServerClient] for whatever server is configured at the moment, or null when none is. */
class ServerAccess(
    private val settings: SettingsStore,
    private val http: OkHttpClient = ServerClient.defaultHttpClient,
) {
    suspend fun client(): ServerClient? {
        val config = settings.currentServer() ?: return null
        return try {
            ServerClient(config, http)
        } catch (e: IllegalArgumentException) {
            null
        }
    }
}

package org.bit63.albumarchiver.upload

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.bit63.albumarchiver.data.ServerConfig
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.util.concurrent.TimeUnit

/** The outcome of one request, before the caller decides what it means. */
sealed interface ApiResult<out T> {
    data class Ok<T>(val value: T, val code: Int = 200) : ApiResult<T>
    /** 401 or 403: the token was rejected (Requirement 8.8). */
    data object AuthFailed : ApiResult<Nothing>
    /** Any other non-2xx status. */
    data class HttpError(val code: Int) : ApiResult<Nothing>
    /** No response at all: unreachable, timed out, connection dropped. */
    data class NetworkError(val cause: IOException) : ApiResult<Nothing>
}

@Serializable
data class AlbumMetadata(
    val name: String,
    @SerialName("page_size") val pageSize: String,
    val pages: List<String>,
    val created: String,
)

@Serializable
data class ServerAlbumSummary(
    val id: String,
    val name: String,
    @SerialName("page_size") val pageSize: String? = null,
    val pages: Int,
    val shots: Int,
    val updated: String,
)

@Serializable
data class ServerAlbumList(val albums: List<ServerAlbumSummary>)

@Serializable
data class ServerPageRef(val id: String, val shots: Int)

@Serializable
data class ServerAlbum(
    val id: String,
    val name: String,
    @SerialName("page_size") val pageSize: String? = null,
    val created: String,
    val pages: List<ServerPageRef>,
)

@Serializable
data class MoveRequest(val shots: List<String>)

@Serializable
data class ServerShot(val id: String, val sha256: String, val bytes: Long, val taken: String)

@Serializable
data class ServerPage(val id: String, val shots: List<ServerShot>)

/** A downloaded body. The caller must close [stream]. */
class Download(val stream: InputStream, val sha256: String?, private val response: Response) : AutoCloseable {
    override fun close() = response.close()
}

/**
 * The requests of the server contract (design, "Server contract"). Every
 * request carries `Authorization: Bearer <token>`; ids are UUIDs the app chose.
 */
class ServerClient(
    private val config: ServerConfig,
    private val http: OkHttpClient = defaultHttpClient,
) {
    private val base: HttpUrl = config.url.trimEnd('/').toHttpUrlOrNull()
        ?: throw IllegalArgumentException("Bad server URL")

    private fun url(vararg segments: String): HttpUrl.Builder =
        base.newBuilder().addPathSegments("api/v1").apply { segments.forEach { addPathSegment(it) } }

    private fun request(url: HttpUrl) = Request.Builder().url(url).header("Authorization", "Bearer ${config.token}")

    suspend fun ping(): ApiResult<Unit> = call(request(url("ping").build()).get().build()) { }

    suspend fun listAlbums(): ApiResult<List<ServerAlbumSummary>> =
        call(request(url("albums").build()).get().build()) { json.decodeFromString<ServerAlbumList>(it.body.string()).albums }

    suspend fun getAlbum(albumId: String): ApiResult<ServerAlbum> =
        call(request(url("albums", albumId).build()).get().build()) { json.decodeFromString<ServerAlbum>(it.body.string()) }

    suspend fun getPage(albumId: String, pageId: String): ApiResult<ServerPage> =
        call(request(url("albums", albumId, "pages", pageId).build()).get().build()) {
            json.decodeFromString<ServerPage>(it.body.string())
        }

    suspend fun putAlbum(albumId: String, meta: AlbumMetadata): ApiResult<Unit> {
        val body = json.encodeToString(AlbumMetadata.serializer(), meta).toRequestBody(JSON)
        return call(request(url("albums", albumId).build()).put(body).build()) { }
    }

    suspend fun deleteAlbum(albumId: String): ApiResult<Unit> =
        call(request(url("albums", albumId).build()).delete().build()) { }

    suspend fun deletePage(albumId: String, pageId: String): ApiResult<Unit> =
        call(request(url("albums", albumId, "pages", pageId).build()).delete().build()) { }

    suspend fun putShot(albumId: String, pageId: String, shotId: String, file: File, sha256: String): ApiResult<Unit> {
        val body: RequestBody = file.asRequestBody(JPEG)
        val req = request(url("albums", albumId, "pages", pageId, "shots", shotId).build())
            .header(SHA_HEADER, sha256)
            .put(body)
            .build()
        return call(req) { }
    }

    /** Moves the listed shots of the album to [pageId]; shots already there or unknown to the server are skipped. */
    suspend fun moveShots(albumId: String, pageId: String, shotIds: List<String>): ApiResult<Unit> {
        val body = json.encodeToString(MoveRequest.serializer(), MoveRequest(shotIds)).toRequestBody(JSON)
        return call(request(url("albums", albumId, "pages", pageId, "move").build()).post(body).build()) { }
    }

    suspend fun deleteShot(albumId: String, pageId: String, shotId: String): ApiResult<Unit> =
        call(request(url("albums", albumId, "pages", pageId, "shots", shotId).build()).delete().build()) { }

    /** Opens a shot's JPEG; with [thumb], the server's preview of at most 320 px. */
    suspend fun getShot(albumId: String, pageId: String, shotId: String, thumb: Boolean = false): ApiResult<Download> {
        val u = url("albums", albumId, "pages", pageId, "shots", shotId)
        if (thumb) u.addQueryParameter("size", "thumb")
        return call(request(u.build()).get().build(), closeOnSuccess = false) {
            Download(it.body.byteStream(), it.header(SHA_HEADER), it)
        }
    }

    private suspend fun <T> call(
        request: Request,
        closeOnSuccess: Boolean = true,
        parse: (Response) -> T,
    ): ApiResult<T> = withContext(Dispatchers.IO) {
        val response = try {
            http.newCall(request).execute()
        } catch (e: IOException) {
            return@withContext ApiResult.NetworkError(e)
        }
        try {
            when {
                response.isSuccessful -> {
                    val value = try {
                        parse(response)
                    } catch (e: IOException) {
                        response.close()
                        return@withContext ApiResult.NetworkError(e)
                    } catch (e: kotlinx.serialization.SerializationException) {
                        response.close()
                        return@withContext ApiResult.HttpError(response.code)
                    }
                    if (closeOnSuccess) response.close()
                    ApiResult.Ok(value, response.code)
                }
                response.code == 401 || response.code == 403 -> { response.close(); ApiResult.AuthFailed }
                else -> { response.close(); ApiResult.HttpError(response.code) }
            }
        } catch (e: RuntimeException) {
            response.close()
            throw e
        }
    }

    companion object {
        const val SHA_HEADER = "X-Content-SHA256"
        private val JSON = "application/json".toMediaType()
        private val JPEG = "image/jpeg".toMediaType()

        val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

        val defaultHttpClient: OkHttpClient by lazy {
            OkHttpClient.Builder()
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .writeTimeout(120, TimeUnit.SECONDS)
                .build()
        }

        /** Checks a server URL as typed in Settings; returns a problem to show, or null. */
        fun urlProblem(url: String): String? {
            val t = url.trim()
            if (t.isEmpty()) return null
            val parsed = t.toHttpUrlOrNull() ?: return "Enter a URL starting with http:// or https://"
            if (parsed.host.isEmpty()) return "The URL needs a host name"
            return null
        }

        /** True when the URL sends the token and photos without encryption (Requirement 9.4). */
        fun isCleartext(url: String): Boolean = url.trim().lowercase().startsWith("http://")
    }
}

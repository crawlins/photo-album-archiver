package org.bit63.albumarchiver.upload

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException

/**
 * Fetches a page's preview (the composed page at 1500 px) into the app's
 * cache, keyed by the run that made it, so a page processed again is fetched
 * again and an unchanged one is not (overlap coverage spec, Requirement
 * 6.7). The last one fetched is kept until the cache is cleared.
 */
class PreviewFetcher(private val cacheDir: File, private val server: ServerAccess) {
    private fun image(pageId: String) = File(cacheDir, "$pageId.jpg")
    private fun stamp(pageId: String) = File(cacheDir, "$pageId.run")

    /** The cached preview when it is from the run [lastRun]; null otherwise. */
    fun cached(pageId: String, lastRun: String?): File? {
        val f = image(pageId)
        if (!f.isFile) return null
        val run = try { stamp(pageId).readText() } catch (e: IOException) { "" }
        return f.takeIf { run == (lastRun ?: "") }
    }

    /** The preview from the run [lastRun]: from the cache, else fetched; null when it could not be fetched. */
    suspend fun fetch(albumId: String, pageId: String, lastRun: String?): File? {
        cached(pageId, lastRun)?.let { return it }
        val client = server.client() ?: return image(pageId).takeIf { it.isFile }
        val r = client.getPagePreview(albumId, pageId)
        if (r !is ApiResult.Ok) return image(pageId).takeIf { it.isFile }
        return withContext(Dispatchers.IO) {
            try {
                cacheDir.mkdirs()
                val temp = File(cacheDir, "$pageId.jpg.tmp")
                r.value.use { d -> temp.outputStream().use { d.stream.copyTo(it) } }
                if (!temp.renameTo(image(pageId))) return@withContext null
                stamp(pageId).writeText(lastRun ?: "")
                image(pageId)
            } catch (e: IOException) {
                null
            }
        }
    }
}

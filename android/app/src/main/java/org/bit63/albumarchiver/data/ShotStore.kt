package org.bit63.albumarchiver.data

import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest

/**
 * Where shots live on the phone, and how they get there safely.
 *
 * Layout: `<root>/<albumId>/<pageId>/<shotId>.jpg`. Page order lives in the
 * database, so renumbering pages never renames a folder. Every write goes to
 * a `.tmp` name, is fsynced, and is renamed into place only once complete, so
 * an interrupted save never leaves a partial shot (Requirement 4.8).
 */
class ShotStore(
    val root: File,
    private val freeBytes: () -> Long = { root.usableSpace },
) {
    fun albumDir(albumId: String) = File(root, albumId)
    fun pageDir(albumId: String, pageId: String) = File(albumDir(albumId), pageId)
    fun shotFile(albumId: String, pageId: String, shotId: String) =
        File(pageDir(albumId, pageId), "$shotId.jpg")

    /** A fresh temp file for a capture still in flight, outside any page folder. */
    fun newTempFile(shotId: String): File {
        val dir = File(root, TEMP_DIR).apply { mkdirs() }
        return File(dir, "$shotId.jpg$TEMP_SUFFIX")
    }

    /** Facts about a completed temp file, computed before it is committed. */
    data class Written(val file: File, val sha256: String, val bytes: Long)

    /** Fsyncs a finished temp file and returns its hash and size. */
    fun finish(temp: File): Written {
        if (!temp.isFile || temp.length() == 0L) throw IOException("Shot file is missing or empty")
        FileOutputStream(temp, true).use { it.fd.sync() }
        return Written(temp, sha256(temp), temp.length())
    }

    /** Renames a finished temp file to [dest], creating its folder. */
    fun commit(temp: File, dest: File) {
        dest.parentFile?.mkdirs()
        if (!temp.renameTo(dest)) throw IOException("Could not move shot into place")
    }

    /**
     * Writes [input] to [dest] through a temp file in the same folder, checking
     * it against [expectedSha256] before the rename. Returns false and leaves
     * nothing behind when the hash differs.
     */
    fun writeVerified(input: InputStream, dest: File, expectedSha256: String): Boolean {
        dest.parentFile?.mkdirs()
        val temp = File(dest.parentFile, dest.name + TEMP_SUFFIX)
        val digest = MessageDigest.getInstance("SHA-256")
        try {
            FileOutputStream(temp).use { out ->
                val buf = ByteArray(64 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    digest.update(buf, 0, n)
                    out.write(buf, 0, n)
                }
                out.fd.sync()
            }
        } catch (e: IOException) {
            temp.delete()
            throw e
        }
        if (!digest.digest().toHex().equals(expectedSha256, ignoreCase = true)) {
            temp.delete()
            return false
        }
        if (!temp.renameTo(dest)) {
            temp.delete()
            throw IOException("Could not move shot into place")
        }
        return true
    }

    fun deleteShotFile(path: String) {
        File(path).delete()
    }

    fun deletePage(albumId: String, pageId: String) {
        pageDir(albumId, pageId).deleteRecursively()
    }

    fun deleteAlbum(albumId: String) {
        albumDir(albumId).deleteRecursively()
    }

    fun freeBytes(): Long = freeBytes.invoke()

    fun isLowOnSpace(): Boolean = freeBytes() < Limits.LOW_STORAGE_BYTES

    /**
     * Startup cleanup (Requirement 10.4): deletes every temp file, and every
     * shot file that no database row points at (a crash between the rename
     * and the commit, or between a commit and the file deletion after it).
     * Returns the number of files removed.
     */
    fun cleanUp(knownPaths: Set<String>): Int {
        if (!root.exists()) return 0
        var removed = 0
        root.walkBottomUp().forEach { f ->
            when {
                f.isFile && f.name.endsWith(TEMP_SUFFIX) -> if (f.delete()) removed++
                f.isFile && f.name.endsWith(".jpg") && f.absolutePath !in knownPaths ->
                    if (f.delete()) removed++
                f.isDirectory && f != root && f.list().isNullOrEmpty() -> f.delete()
            }
        }
        return removed
    }

    companion object {
        const val TEMP_SUFFIX = ".tmp"
        private const val TEMP_DIR = "capturing"

        fun sha256(file: File): String = file.inputStream().use { sha256(it) }

        fun sha256(input: InputStream): String {
            val digest = MessageDigest.getInstance("SHA-256")
            val buf = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                digest.update(buf, 0, n)
            }
            return digest.digest().toHex()
        }

        private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
    }
}

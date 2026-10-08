package org.bit63.albumarchiver.data

import androidx.room.withTransaction
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import java.io.File
import java.time.Clock
import java.util.UUID

/** Starts the upload worker after a change has queued something for the server. */
fun interface UploadKicker {
    fun kick()
}

sealed interface AddShotResult {
    data class Added(val shot: Shot, val page: Page, val pageCreated: Boolean) : AddShotResult
    data object PageFull : AddShotResult
    data object NoAlbum : AddShotResult
}

sealed interface NextPageResult {
    data class Started(val page: Page) : NextPageResult
    /** The current page has no shots yet, so no new page was made (Requirement 5.2). */
    data object CurrentPageEmpty : NextPageResult
    data object AlbumFull : NextPageResult
    data object NoAlbum : NextPageResult
}

/** What a shot deletion did, for the confirmation and for where the review screen goes next. */
data class ShotDeletion(val pageDeleted: Boolean)

/** What deleting a shot and the shots after it did. */
data class RunDeletion(val count: Int, val pageDeleted: Boolean)

sealed interface MoveResult {
    /** [count] shots went to [page]; [sourceDeleted] when the page they left was emptied and removed. */
    data class Moved(val page: Page, val count: Int, val firstShotId: String, val sourceDeleted: Boolean) : MoveResult
    /** The target page would have more than 25 shots. */
    data object PageFull : MoveResult
    /** A new page would be the album's 501st. */
    data object AlbumFull : MoveResult
    /** The move would leave the album as it is (the whole page to a page of its own). */
    data object NothingToMove : MoveResult
    data object NotFound : MoveResult
}

/**
 * The only writer of albums, pages, shots and upload ops. Every change is one
 * Room transaction that also queues the matching [UploadOp]s, so the server
 * sees changes in the order they happened (Requirement 8.9). Files are deleted
 * only after the transaction commits; a crash in between leaves orphan files
 * that [ShotStore.cleanUp] removes at the next start.
 */
class AlbumRepository(
    private val db: AppDatabase,
    private val store: ShotStore,
    private val kicker: UploadKicker,
    private val clock: Clock = Clock.systemUTC(),
    private val newId: () -> String = { UUID.randomUUID().toString() },
) {
    private val albums = db.albums()
    private val pages = db.pages()
    private val shots = db.shots()
    private val ops = db.ops()

    // ---- Reads ----

    fun observeAlbum(id: String): Flow<Album?> = albums.observe(id)
    fun observeSummaries(): Flow<List<AlbumSummary>> = albums.observeSummaries()
    fun observeAlbumIds(): Flow<List<String>> = albums.observeIds()
    fun observePages(albumId: String): Flow<List<Page>> = pages.observeForAlbum(albumId)
    fun observeLastPage(albumId: String): Flow<Page?> = pages.observeLast(albumId)
    fun observeShots(pageId: String): Flow<List<Shot>> = shots.observeForPage(pageId)
    fun observeAlbumShots(albumId: String): Flow<List<Shot>> = shots.observeForAlbum(albumId)
    fun observePendingShots(albumId: String): Flow<Int> = ops.observePendingShots(albumId)
    fun observeQueueHeadAlbum(): Flow<String?> = ops.observeHead().map { it?.albumId }

    suspend fun album(id: String): Album? = albums.get(id)
    suspend fun page(id: String): Page? = pages.get(id)
    suspend fun pages(albumId: String): List<Page> = pages.forAlbum(albumId)
    suspend fun lastPage(albumId: String): Page? = pages.last(albumId)
    suspend fun shots(pageId: String): List<Shot> = shots.forPage(pageId)
    suspend fun shot(id: String): Shot? = shots.get(id)

    // ---- Albums ----

    /** Creates an empty album and queues its metadata, so it reaches the server before any shot (Requirement 8.10). */
    suspend fun createAlbum(name: String, pageSize: PageSize): Album {
        val album = Album(newId(), name.trim(), pageSize.text, clock.instant())
        db.withTransaction {
            albums.insert(album)
            queueMetadata(album.id)
        }
        kicker.kick()
        return album
    }

    /** Renames or resizes an album; pages and shots are untouched (Requirement 2.9). */
    suspend fun updateAlbum(id: String, name: String, pageSize: PageSize): Boolean {
        val changed = db.withTransaction {
            val album = albums.get(id) ?: return@withTransaction false
            albums.update(album.copy(name = name.trim(), pageSize = pageSize.text))
            queueMetadata(id)
            true
        }
        if (changed) kicker.kick()
        return changed
    }

    /**
     * Removes an album and its shots from the phone. Its pending uploads are
     * dropped either way, since their files are going; with [alsoOnServer] a
     * `DELETE_ALBUM` op is queued in the same transaction so nothing queued
     * earlier can recreate the album after it (Requirement 13.7).
     */
    suspend fun deleteAlbum(id: String, alsoOnServer: Boolean) {
        db.withTransaction {
            ops.deleteForAlbum(id)
            albums.delete(id) // pages and shots cascade
            if (alsoOnServer) ops.insert(UploadOp(kind = OpKind.DELETE_ALBUM, albumId = id))
        }
        store.deleteAlbum(id)
        if (alsoOnServer) kicker.kick()
    }

    // ---- Shots and pages ----

    /**
     * Adds a captured shot to the album's current (last) page, creating page 1
     * when the album has none (Requirement 4.2). [written] is a finished temp
     * file; it is renamed into the page's folder inside the transaction, just
     * before the row is inserted, so a row never exists without its file
     * (Requirement 10.3). On [AddShotResult.PageFull] or [AddShotResult.NoAlbum]
     * the temp file is left for the caller to delete.
     */
    suspend fun addShot(albumId: String, shotId: String, written: ShotStore.Written): AddShotResult {
        val result = db.withTransaction {
            albums.get(albumId) ?: return@withTransaction AddShotResult.NoAlbum
            var page = pages.last(albumId)
            var created = false
            if (page == null) {
                page = Page(newId(), albumId, position = 1)
                pages.insert(page)
                queueMetadata(albumId)
                created = true
            }
            if (page.shotCount >= Limits.MAX_SHOTS_PER_PAGE) return@withTransaction AddShotResult.PageFull
            val dest = store.shotFile(albumId, page.id, shotId)
            store.commit(written.file, dest)
            val shot = Shot(
                id = shotId, albumId = albumId, pageId = page.id, path = dest.absolutePath,
                sha256 = written.sha256, bytes = written.bytes, takenAt = clock.instant(),
            )
            shots.insert(shot)
            val updated = page.copy(shotCount = page.shotCount + 1)
            pages.setShotCount(page.id, updated.shotCount)
            ops.insert(UploadOp(kind = OpKind.PUT_SHOT, albumId = albumId, pageId = page.id, shotId = shotId))
            AddShotResult.Added(shot, updated, created)
        }
        if (result is AddShotResult.Added) kicker.kick()
        return result
    }

    /**
     * Starts a new page after the current one (Requirement 5.1). The album's
     * metadata is queued after every shot of the page being left, so when the
     * server sees the new page it already has the previous page's shots.
     */
    suspend fun startNextPage(albumId: String): NextPageResult {
        val result = db.withTransaction {
            albums.get(albumId) ?: return@withTransaction NextPageResult.NoAlbum
            val current = pages.last(albumId)
            if (current == null || current.shotCount == 0) return@withTransaction NextPageResult.CurrentPageEmpty
            if (current.position >= Limits.MAX_PAGES_PER_ALBUM) return@withTransaction NextPageResult.AlbumFull
            val page = Page(newId(), albumId, current.position + 1)
            pages.insert(page)
            queueMetadata(albumId)
            NextPageResult.Started(page)
        }
        if (result is NextPageResult.Started) kicker.kick()
        return result
    }

    /** Removes a page just started by "Next page", as long as it is still the last page and has no shots (Requirement 5.4). */
    suspend fun undoNextPage(pageId: String): Boolean {
        val undone = db.withTransaction {
            val page = pages.get(pageId) ?: return@withTransaction false
            val last = pages.last(page.albumId)
            if (last?.id != pageId || page.shotCount != 0 || shots.countForPage(pageId) != 0) {
                return@withTransaction false
            }
            pages.delete(pageId)
            queueMetadata(page.albumId)
            true
        }
        if (undone) kicker.kick()
        return undone
    }

    /**
     * Deletes one shot (Requirement 7.3). When it was the only shot of a page
     * other than the last, the page goes too (Requirement 12.4); the last page
     * is exempt because it is the current page, and an empty current page is
     * the same state as just after "Next page".
     */
    suspend fun deleteShot(shotId: String): ShotDeletion? {
        var filePath: String? = null
        var pageToRemove: Page? = null
        val result = db.withTransaction {
            val shot = shots.get(shotId) ?: return@withTransaction null
            val page = pages.get(shot.pageId) ?: return@withTransaction null
            shots.delete(shotId)
            filePath = shot.path
            val remaining = (page.shotCount - 1).coerceAtLeast(0)
            pages.setShotCount(page.id, remaining)
            // A shot never confirmed by the server needs no upload any more, but
            // the deletion is still sent in case the upload landed unconfirmed.
            ops.deletePutShot(shotId)
            ops.insert(UploadOp(kind = OpKind.DELETE_SHOT, albumId = page.albumId, pageId = page.id, shotId = shotId))
            val isLast = pages.last(page.albumId)?.id == page.id
            if (remaining == 0 && !isLast) {
                removePageInTransaction(page)
                pageToRemove = page
                ShotDeletion(pageDeleted = true)
            } else {
                ShotDeletion(pageDeleted = false)
            }
        }
        filePath?.let(store::deleteShotFile)
        pageToRemove?.let { store.deletePage(it.albumId, it.id) }
        if (result != null) kicker.kick()
        return result
    }

    /** The shot [shotId] and every later shot of its page, in the order they were taken; empty when it is gone. */
    suspend fun runOf(shotId: String): List<Shot> {
        val shot = shots.get(shotId) ?: return emptyList()
        return shots.forPage(shot.pageId).dropWhile { it.id != shotId }
    }

    /**
     * Deletes a shot and every later shot of its page in one transaction,
     * each as [deleteShot] would; an inner page left empty goes too, the last
     * page stays as the empty current page.
     */
    suspend fun deleteRun(shotId: String): RunDeletion? {
        var paths = emptyList<String>()
        var pageToRemove: Page? = null
        val result = db.withTransaction {
            val shot = shots.get(shotId) ?: return@withTransaction null
            val page = pages.get(shot.pageId) ?: return@withTransaction null
            val run = runOf(shotId)
            for (s in run) {
                shots.delete(s.id)
                ops.deletePutShot(s.id)
                ops.insert(UploadOp(kind = OpKind.DELETE_SHOT, albumId = page.albumId, pageId = page.id, shotId = s.id))
            }
            paths = run.map { it.path }
            val remaining = (page.shotCount - run.size).coerceAtLeast(0)
            pages.setShotCount(page.id, remaining)
            val isLast = pages.last(page.albumId)?.id == page.id
            if (remaining == 0 && !isLast) {
                removePageInTransaction(page)
                pageToRemove = page
            }
            RunDeletion(run.size, pageDeleted = pageToRemove != null)
        }
        paths.forEach(store::deleteShotFile)
        pageToRemove?.let { store.deletePage(it.albumId, it.id) }
        if (result != null) kicker.kick()
        return result
    }

    /**
     * Moves a shot and the shots after it to the next page, or to a new page
     * after the album's last page (shot-actions Requirement 5).
     */
    suspend fun moveRunToNextPage(shotId: String): MoveResult = moveRun(shotId, split = false)

    /** Moves a shot and the shots after it to a new page inserted after theirs (shot-actions Requirement 6). */
    suspend fun splitRunToNewPage(shotId: String): MoveResult = moveRun(shotId, split = true)

    /**
     * Each moved shot's file gets a second name in the target page's folder
     * inside the transaction that records it there, and the old name goes
     * after the commit, so a crash at any point leaves one name the database
     * does not use, which startup cleanup removes. The server gets one
     * `MOVE_SHOTS` per shot, before the `DELETE_PAGE` of an emptied page and
     * the metadata with the new page order. A shot whose upload is still
     * queued is uploaded to the target page and moved as well, in case an
     * earlier attempt reached the server unconfirmed.
     */
    private suspend fun moveRun(shotId: String, split: Boolean): MoveResult {
        val linked = mutableListOf<File>()
        var oldPaths = emptyList<String>()
        var removed: Page? = null
        val result = try {
            db.withTransaction<MoveResult> {
                val shot = shots.get(shotId) ?: return@withTransaction MoveResult.NotFound
                val source = pages.get(shot.pageId) ?: return@withTransaction MoveResult.NotFound
                val albumId = source.albumId
                val run = runOf(shotId)
                val whole = run.size >= source.shotCount
                val last = pages.last(albumId)!!
                val next = if (split || last.id == source.id) null else pages.atPosition(albumId, source.position + 1)
                if (whole && next == null) return@withTransaction MoveResult.NothingToMove
                if (next == null && last.position >= Limits.MAX_PAGES_PER_ALBUM) return@withTransaction MoveResult.AlbumFull
                if (next != null && next.shotCount + run.size > Limits.MAX_SHOTS_PER_PAGE) return@withTransaction MoveResult.PageFull

                val target = next ?: Page(newId(), albumId, source.position + 1).also {
                    pages.shiftUpStep1(albumId, source.position)
                    pages.shiftDownStep2(albumId)
                    pages.insert(it)
                }
                for (s in run) {
                    val dest = store.shotFile(albumId, target.id, s.id)
                    if (s.state == ShotState.PRESENT) {
                        store.link(File(s.path), dest)
                        linked += dest
                    }
                    shots.moveTo(s.id, target.id, dest.absolutePath)
                    ops.retargetPutShot(s.id, target.id)
                }
                oldPaths = run.filter { it.state == ShotState.PRESENT }.map { it.path }
                val moved = target.copy(shotCount = target.shotCount + run.size)
                pages.setShotCount(target.id, moved.shotCount)
                pages.setShotCount(source.id, source.shotCount - run.size)
                for (s in run) {
                    ops.insert(UploadOp(kind = OpKind.MOVE_SHOTS, albumId = albumId, pageId = target.id, shotId = s.id))
                }
                if (whole) {
                    removePageInTransaction(source)
                    removed = source
                } else {
                    queueMetadata(albumId)
                }
                MoveResult.Moved(pages.get(target.id) ?: moved, run.size, run.first().id, sourceDeleted = whole)
            }
        } catch (e: Throwable) {
            linked.forEach { it.delete() }
            throw e
        }
        if (result is MoveResult.Moved) {
            oldPaths.forEach(store::deleteShotFile)
            removed?.let { store.deletePage(it.albumId, it.id) }
            kicker.kick()
        }
        return result
    }

    /**
     * Deletes a page and its shots and renumbers the pages after it so page
     * numbers stay contiguous from 1 (Requirement 12.3). Later pages keep their
     * ids, folders and server paths; only their positions change.
     */
    suspend fun deletePage(pageId: String): Boolean {
        var removed: Page? = null
        db.withTransaction {
            val page = pages.get(pageId) ?: return@withTransaction
            removePageInTransaction(page)
            removed = page
        }
        val page = removed ?: return false
        store.deletePage(page.albumId, page.id)
        kicker.kick()
        return true
    }

    /** Queues the album's metadata with its page order as it is now; call inside the transaction that changed it. */
    private suspend fun queueMetadata(albumId: String) {
        val order = pages.forAlbum(albumId).joinToString(",") { it.id }
        ops.insert(UploadOp(kind = OpKind.ALBUM_META, albumId = albumId, pages = order))
    }

    private suspend fun removePageInTransaction(page: Page) {
        ops.deletePutShotsForPage(page.id)
        pages.delete(page.id) // shots cascade
        pages.shiftDownStep1(page.albumId, page.position)
        pages.shiftDownStep2(page.albumId)
        ops.insert(UploadOp(kind = OpKind.DELETE_PAGE, albumId = page.albumId, pageId = page.id))
        queueMetadata(page.albumId)
    }

    // ---- Albums opened from the server ----

    /**
     * Writes an album fetched from the server, with its pages marked as not yet
     * loaded and no upload ops, so nothing is sent back (Requirement 14.1).
     * With [sendMetadata] the album's metadata is queued after all: the server
     * had no page size for it, so the one the phone filled in has to reach the
     * server or it never processes the album.
     */
    suspend fun importAlbum(album: Album, pageCounts: List<Pair<String, Int>>, sendMetadata: Boolean = false) {
        db.withTransaction {
            albums.insert(album)
            pages.insertAll(pageCounts.mapIndexed { i, (id, count) ->
                Page(id, album.id, position = i + 1, shotCount = count, shotsLoaded = false)
            })
            if (sendMetadata) queueMetadata(album.id)
        }
        if (sendMetadata) kicker.kick()
    }

    /** Records a server page's shot list as `NOT_DOWNLOADED` rows (Requirement 14.3). */
    suspend fun recordPageShots(pageId: String, remote: List<RemoteShot>) {
        db.withTransaction {
            val page = pages.get(pageId) ?: return@withTransaction
            shots.insertIgnore(remote.map {
                Shot(
                    id = it.id, albumId = page.albumId, pageId = pageId,
                    path = store.shotFile(page.albumId, pageId, it.id).absolutePath,
                    sha256 = it.sha256, bytes = it.bytes, takenAt = it.takenAt,
                    state = ShotState.NOT_DOWNLOADED,
                )
            })
            pages.markLoaded(pageId, shots.countForPage(pageId))
        }
    }

    /** Marks a fetched shot as present; from then on it is an ordinary local file. */
    suspend fun markShotPresent(shotId: String) = shots.setState(shotId, ShotState.PRESENT)

    /** Removes the row of a shot the server no longer has, without queuing anything. */
    suspend fun forgetMissingShot(shotId: String) {
        db.withTransaction {
            val shot = shots.get(shotId) ?: return@withTransaction
            shots.delete(shotId)
            pages.setShotCount(shot.pageId, shots.countForPage(shot.pageId))
        }
    }

    /** Every shot file path the database knows, for [ShotStore.cleanUp]. */
    suspend fun knownPaths(): Set<String> = shots.allPaths().map { File(it).absolutePath }.toSet()
}

/** One entry of a server page's shot list. */
data class RemoteShot(val id: String, val sha256: String, val bytes: Long, val takenAt: java.time.Instant)

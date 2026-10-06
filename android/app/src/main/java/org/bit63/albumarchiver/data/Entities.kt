package org.bit63.albumarchiver.data

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import java.time.Instant

/** One physical photo album. [pageSize] is the backend's size string, e.g. `8.5x11in`. */
@Entity(tableName = "album")
data class Album(
    @PrimaryKey val id: String,
    val name: String,
    val pageSize: String,
    val createdAt: Instant,
)

/**
 * One album page. [position] is the displayed, 1-based page number; it is
 * rewritten when an earlier page is deleted, while [id] (and with it the
 * page's folder and server path) never changes.
 *
 * [shotCount] is the number of shots on the page. For pages created on this
 * phone it always equals the number of [Shot] rows; for a page of an album
 * opened from the server it holds the server's count until [shotsLoaded]
 * becomes true, and is kept in step with the rows after that.
 */
@Entity(
    tableName = "page",
    foreignKeys = [ForeignKey(
        entity = Album::class,
        parentColumns = ["id"],
        childColumns = ["albumId"],
        onDelete = ForeignKey.CASCADE,
    )],
    indices = [Index(value = ["albumId", "position"], unique = true)],
)
data class Page(
    @PrimaryKey val id: String,
    val albumId: String,
    val position: Int,
    val shotCount: Int = 0,
    val shotsLoaded: Boolean = true,
)

/** Whether a shot's JPEG is on the phone. Only shots of server albums can be missing. */
enum class ShotState { PRESENT, NOT_DOWNLOADED }

@Entity(
    tableName = "shot",
    foreignKeys = [ForeignKey(
        entity = Page::class,
        parentColumns = ["id"],
        childColumns = ["pageId"],
        onDelete = ForeignKey.CASCADE,
    )],
    indices = [Index("pageId"), Index("albumId")],
)
data class Shot(
    @PrimaryKey val id: String,
    val albumId: String,
    val pageId: String,
    /** Absolute path of the JPEG, whether or not it has been fetched yet. */
    val path: String,
    val sha256: String,
    val bytes: Long,
    val takenAt: Instant,
    val state: ShotState = ShotState.PRESENT,
)

enum class OpKind { ALBUM_META, PUT_SHOT, DELETE_SHOT, DELETE_PAGE, DELETE_ALBUM }

/**
 * One change waiting to reach the server. Rows are sent strictly in [seq]
 * order (Requirement 8.9). An `ALBUM_META` op records the album's page order
 * in [pages] when it is queued, because the server treats a page appearing
 * as "the previous page is finished": sending the current order instead
 * would announce a page before the shots queued ahead of it. The album's
 * name and page size are read when the op is sent, so they are the latest.
 */
@Entity(tableName = "upload_op", indices = [Index("albumId")])
data class UploadOp(
    @PrimaryKey(autoGenerate = true) val seq: Long = 0,
    val kind: OpKind,
    val albumId: String,
    val pageId: String? = null,
    val shotId: String? = null,
    val attempts: Int = 0,
    /** `ALBUM_META` only: the page ids in order, comma-separated; null on ops queued before this was recorded. */
    val pages: String? = null,
)

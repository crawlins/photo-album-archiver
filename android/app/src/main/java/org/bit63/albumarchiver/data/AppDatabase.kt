package org.bit63.albumarchiver.data

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverter
import androidx.room.TypeConverters
import androidx.room.Update
import kotlinx.coroutines.flow.Flow
import java.time.Instant

class Converters {
    @TypeConverter fun instantToMillis(value: Instant): Long = value.toEpochMilli()
    @TypeConverter fun millisToInstant(value: Long): Instant = Instant.ofEpochMilli(value)
}

/** An album with the numbers the drawer shows. */
data class AlbumSummary(
    val id: String,
    val name: String,
    val pageSize: String,
    val createdAt: Instant,
    val pageCount: Int,
    val pendingOps: Int,
)

@Dao
interface AlbumDao {
    @Insert suspend fun insert(album: Album)
    @Update suspend fun update(album: Album)
    @Query("DELETE FROM album WHERE id = :id") suspend fun delete(id: String)
    @Query("SELECT * FROM album WHERE id = :id") suspend fun get(id: String): Album?
    @Query("SELECT * FROM album WHERE id = :id") fun observe(id: String): Flow<Album?>
    @Query("SELECT id FROM album") suspend fun ids(): List<String>
    @Query("SELECT id FROM album") fun observeIds(): Flow<List<String>>

    @Query(
        """
        SELECT a.id, a.name, a.pageSize, a.createdAt,
               (SELECT COUNT(*) FROM page p WHERE p.albumId = a.id) AS pageCount,
               (SELECT COUNT(*) FROM upload_op o WHERE o.albumId = a.id) AS pendingOps
        FROM album a ORDER BY a.createdAt DESC
        """
    )
    fun observeSummaries(): Flow<List<AlbumSummary>>
}

@Dao
interface PageDao {
    @Insert suspend fun insert(page: Page)
    @Insert suspend fun insertAll(pages: List<Page>)
    @Query("DELETE FROM page WHERE id = :id") suspend fun delete(id: String)
    @Query("SELECT * FROM page WHERE id = :id") suspend fun get(id: String): Page?
    @Query("SELECT * FROM page WHERE id = :id") fun observe(id: String): Flow<Page?>
    @Query("SELECT * FROM page WHERE albumId = :albumId ORDER BY position")
    suspend fun forAlbum(albumId: String): List<Page>
    @Query("SELECT * FROM page WHERE albumId = :albumId ORDER BY position")
    fun observeForAlbum(albumId: String): Flow<List<Page>>
    @Query("SELECT * FROM page WHERE albumId = :albumId ORDER BY position DESC LIMIT 1")
    suspend fun last(albumId: String): Page?
    @Query("SELECT * FROM page WHERE albumId = :albumId ORDER BY position DESC LIMIT 1")
    fun observeLast(albumId: String): Flow<Page?>
    @Query("UPDATE page SET shotCount = :count WHERE id = :id")
    suspend fun setShotCount(id: String, count: Int)
    @Query("UPDATE page SET shotsLoaded = 1, shotCount = :count WHERE id = :id")
    suspend fun markLoaded(id: String, count: Int)

    /**
     * Closes the gap left by deleting the page at [deleted]. SQLite checks
     * the unique (albumId, position) index row by row, so shifting in place
     * could collide; the positions go through negative values first.
     */
    @Query("UPDATE page SET position = -(position - 1) WHERE albumId = :albumId AND position > :deleted")
    suspend fun shiftDownStep1(albumId: String, deleted: Int)
    @Query("UPDATE page SET position = -position WHERE albumId = :albumId AND position < 0")
    suspend fun shiftDownStep2(albumId: String)
}

@Dao
interface ShotDao {
    @Insert suspend fun insert(shot: Shot)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun insertIgnore(shots: List<Shot>)
    @Query("DELETE FROM shot WHERE id = :id") suspend fun delete(id: String)
    @Query("SELECT * FROM shot WHERE id = :id") suspend fun get(id: String): Shot?
    @Query("SELECT * FROM shot WHERE pageId = :pageId ORDER BY takenAt, rowid")
    suspend fun forPage(pageId: String): List<Shot>
    @Query("SELECT * FROM shot WHERE pageId = :pageId ORDER BY takenAt, rowid")
    fun observeForPage(pageId: String): Flow<List<Shot>>
    @Query("SELECT * FROM shot WHERE albumId = :albumId ORDER BY takenAt, rowid")
    fun observeForAlbum(albumId: String): Flow<List<Shot>>
    @Query("SELECT COUNT(*) FROM shot WHERE pageId = :pageId") suspend fun countForPage(pageId: String): Int
    @Query("SELECT path FROM shot") suspend fun allPaths(): List<String>
    @Query("UPDATE shot SET state = :state WHERE id = :id") suspend fun setState(id: String, state: ShotState)
}

@Dao
interface UploadOpDao {
    @Insert suspend fun insert(op: UploadOp): Long
    @Query("SELECT * FROM upload_op ORDER BY seq LIMIT 1") suspend fun head(): UploadOp?
    @Query("SELECT * FROM upload_op ORDER BY seq") suspend fun all(): List<UploadOp>
    @Query("SELECT * FROM upload_op ORDER BY seq LIMIT 1") fun observeHead(): Flow<UploadOp?>
    @Query("DELETE FROM upload_op WHERE seq = :seq") suspend fun delete(seq: Long)
    @Query("DELETE FROM upload_op WHERE albumId = :albumId") suspend fun deleteForAlbum(albumId: String)
    @Query("DELETE FROM upload_op WHERE kind = 'PUT_SHOT' AND shotId = :shotId")
    suspend fun deletePutShot(shotId: String)
    @Query("DELETE FROM upload_op WHERE kind = 'PUT_SHOT' AND pageId = :pageId")
    suspend fun deletePutShotsForPage(pageId: String)
    @Query("UPDATE upload_op SET attempts = attempts + 1 WHERE seq = :seq")
    suspend fun incrementAttempts(seq: Long)
    @Query("SELECT COUNT(*) FROM upload_op") fun observeCount(): Flow<Int>
    @Query("SELECT COUNT(*) FROM upload_op WHERE kind = 'PUT_SHOT' AND albumId = :albumId")
    fun observePendingShots(albumId: String): Flow<Int>
}

@Database(entities = [Album::class, Page::class, Shot::class, UploadOp::class], version = 1)
@TypeConverters(Converters::class)
abstract class AppDatabase : RoomDatabase() {
    abstract fun albums(): AlbumDao
    abstract fun pages(): PageDao
    abstract fun shots(): ShotDao
    abstract fun ops(): UploadOpDao

    companion object {
        fun open(context: Context): AppDatabase =
            Room.databaseBuilder(context, AppDatabase::class.java, "albums.db").build()

        fun inMemory(context: Context): AppDatabase =
            Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java)
                .allowMainThreadQueries()
                .build()
    }
}

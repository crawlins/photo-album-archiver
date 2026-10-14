package org.bit63.albumarchiver.ui.page

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.bit63.albumarchiver.data.AlbumRepository
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.PageFeedback
import org.bit63.albumarchiver.data.PageWarning
import org.bit63.albumarchiver.data.Shot
import org.bit63.albumarchiver.data.ShotRect
import org.bit63.albumarchiver.data.firstShotWithBox
import org.bit63.albumarchiver.data.pageRects
import org.bit63.albumarchiver.data.parseOutlines
import org.bit63.albumarchiver.upload.FeedbackPoller
import org.bit63.albumarchiver.upload.PreviewFetcher
import java.io.File
import kotlin.coroutines.CoroutineContext

/** A point as fractions of the composed page's width and height. */
data class PagePoint(val x: Float, val y: Float)

/** One shot's outline on the composed page, numbered as in the thumbnail strip (1-based). */
data class Outline(val shotId: String, val number: Int, val points: List<PagePoint>)

/** True when [p] lies inside the polygon [points] (even-odd rule). */
fun contains(points: List<PagePoint>, p: PagePoint): Boolean {
    if (points.size < 3) return false
    var inside = false
    var j = points.size - 1
    for (i in points.indices) {
        val a = points[i]
        val b = points[j]
        if ((a.y > p.y) != (b.y > p.y) && p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x) inside = !inside
        j = i
    }
    return inside
}

/** The smallest outline under [p], so a shot inside a wider one can still be picked; null when none is. */
fun outlineAt(outlines: List<Outline>, p: PagePoint): Outline? =
    outlines.filter { contains(it.points, p) }.minByOrNull { area(it.points) }

/** The rectangle under [p], if any (the first in the warnings' order). */
fun rectAt(rects: List<ShotRect>, p: PagePoint): ShotRect? =
    rects.firstOrNull { p.x in it.left..it.right && p.y in it.top..it.bottom }

private fun area(points: List<PagePoint>): Float {
    var s = 0f
    for (i in points.indices) {
        val a = points[i]
        val b = points[(i + 1) % points.size]
        s += a.x * b.y - b.x * a.y
    }
    return kotlin.math.abs(s) / 2
}

data class PageViewState(
    val page: Page? = null,
    val shots: List<Shot> = emptyList(),
    /** The page's feedback; null before any was fetched. */
    val feedback: PageFeedback? = null,
    val warnings: List<PageWarning> = emptyList(),
    val outlines: List<Outline> = emptyList(),
    val rects: List<ShotRect> = emptyList(),
    val selectedShotId: String? = null,
) {
    val hasPreview: Boolean get() = feedback?.preview == true
    val stateWord: String get() = feedback?.state?.takeIf { it.isNotEmpty() } ?: "not processed"

    /** The shot the "Shot" toggle opens: the selection, else the first that shows a warning, else the first (Requirement 6.5). */
    fun shotToOpen(): Int {
        val ids = shots.map { it.id }
        val id = selectedShotId ?: warnings.firstShotWithBox(ids) ?: ids.firstOrNull() ?: return 0
        return ids.indexOf(id).coerceAtLeast(0)
    }
}

/**
 * The composed page on the phone (overlap coverage spec, Requirement 6):
 * the server's preview with every shot's outline and the warning regions
 * drawn on it, a tap selecting a shot or opening the review at it.
 */
class PageViewModel(
    private val repo: AlbumRepository,
    private val previews: PreviewFetcher,
    val albumId: String,
    val pageId: String,
    private val io: CoroutineContext = Dispatchers.IO,
    feedback: FeedbackPoller? = null,
    uploadRunning: Flow<Boolean> = flowOf(false),
) : ViewModel() {
    private val selected = MutableStateFlow<String?>(null)

    init {
        feedback?.start(viewModelScope, flowOf(albumId), uploadRunning)
    }

    val state: StateFlow<PageViewState> = combine(
        repo.observePages(albumId).map { ps -> ps.firstOrNull { it.id == pageId } },
        repo.observeShots(pageId),
        repo.observePageFeedback(pageId),
        repo.observePendingPages(albumId),
        selected,
    ) { page, shots, fb, pending, sel ->
        val uploaded = fb != null && pageId !in pending
        val warnings = if (uploaded) fb!!.warnings else emptyList()
        val byId = shots.map { it.id }
        val outlines = if (uploaded) parseOutlines(fb!!.outlinesJson).mapNotNull { (id, pts) ->
            val n = byId.indexOf(id)
            if (n < 0) null else Outline(id, n + 1, pts.map { PagePoint(it.first, it.second) })
        }.sortedBy { it.number } else emptyList()
        PageViewState(page, shots, fb, warnings, outlines, warnings.pageRects(), sel?.takeIf { it in byId })
    }.stateIn(viewModelScope, SharingStarted.Eagerly, PageViewState())

    private val _preview = MutableStateFlow<File?>(null)
    /** The preview image, once fetched; refetched when the page's last run changes (Requirement 6.7). */
    val preview: StateFlow<File?> = _preview

    init {
        viewModelScope.launch {
            state.map { it.feedback?.let { fb -> fb.preview to fb.lastRun } }.distinctUntilChanged().collect { key ->
                // Cleared first: a refetch lands in the same file, which a StateFlow would not re-emit.
                _preview.value = null
                if (key != null && key.first) _preview.value = withContext(io) { previews.fetch(albumId, pageId, key.second) }
            }
        }
    }

    /** A tap at [p]: a rectangle opens the review at a shot showing it; an outline selects, or opens when already selected. Returns the review index to open, or null. */
    fun tap(p: PagePoint): Int? {
        val s = state.value
        val ids = s.shots.map { it.id }
        rectAt(s.rects, p)?.let { r ->
            val region = s.warnings.flatMap { it.regions }.firstOrNull { reg -> reg.bbox?.let { b -> b.size == 4 && b[0].toFloat() == r.left && b[1].toFloat() == r.top } == true }
            val shot = region?.shotBoxes?.keys?.let { keys -> ids.firstOrNull { it in keys } } ?: ids.firstOrNull() ?: return null
            return ids.indexOf(shot)
        }
        val o = outlineAt(s.outlines, p) ?: run { selected.value = null; return null }
        if (selected.value == o.shotId) return ids.indexOf(o.shotId)
        selected.value = o.shotId
        return null
    }

    fun select(shotId: String?) {
        selected.value = shotId
    }

    /** Makes this page the page new shots go to (Requirement 4.7). */
    fun addShotsToPage() {
        viewModelScope.launch { withContext(io) { repo.setTargetPage(albumId, pageId) } }
    }
}

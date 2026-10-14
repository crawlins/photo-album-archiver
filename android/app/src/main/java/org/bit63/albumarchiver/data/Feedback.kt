package org.bit63.albumarchiver.data

import androidx.room.Entity
import androidx.room.PrimaryKey
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.time.Instant

/**
 * What the server last said about a page: its processing state and the
 * warnings of its last run, as the album status reports them (overlap
 * coverage spec, Requirement 4.2). [warningsJson] is the warnings list as
 * sent, so fields this version of the app does not know are kept.
 */
@Entity(tableName = "page_feedback")
data class PageFeedback(
    @PrimaryKey val pageId: String,
    val albumId: String,
    val state: String,
    val lastRun: String?,
    val warningsJson: String,
    val fetchedAt: Instant,
) {
    val warnings: List<PageWarning> get() = parseWarnings(warningsJson)
}

/** One area a warning is about, with its box in each photo's own pixels, keyed by shot id. */
data class WarningRegion(
    val bbox: List<Double>?,
    val location: String?,
    val shots: List<Int>,
    val cause: String?,
    val shotBoxes: Map<String, List<Int>>,
)

data class PageWarning(
    val code: String,
    val message: String,
    val locations: List<String>,
    val regions: List<WarningRegion>,
) {
    /** Glare and single coverage are outlined in red, uncovered page in cyan. */
    val isRed: Boolean get() = code != CODE_UNCOVERED

    companion object {
        const val CODE_GLARE = "glare"
        const val CODE_UNCOVERED = "incomplete_coverage"
        const val CODE_SINGLE = "single_coverage"

        /** The codes that mark a page and are drawn on its photos (Requirement 4.3). */
        val AREA_CODES = setOf(CODE_GLARE, CODE_UNCOVERED, CODE_SINGLE)
    }
}

/** A rectangle to draw on a photo, as fractions of the photo's width and height. */
data class ShotRect(val left: Float, val top: Float, val right: Float, val bottom: Float, val code: String)

/** One line of the warnings bar: the message, with where to look when the photo shown does not show it. */
data class WarningLine(val code: String, val text: String)

private val lenient = Json { ignoreUnknownKeys = true; isLenient = true }

/**
 * Parses the warnings list tolerantly: a warning without regions, or a
 * region without boxes (pages processed before schema 3, where `regions`
 * was a count), is kept with what it has (Requirement 5.3).
 */
fun parseWarnings(json: String): List<PageWarning> {
    val root = try {
        lenient.parseToJsonElement(json)
    } catch (e: Exception) {
        return emptyList()
    }
    return (root as? JsonArray)?.mapNotNull { parseWarning(it) } ?: emptyList()
}

fun parseWarning(element: JsonElement): PageWarning? {
    val o = element as? JsonObject ?: return null
    val code = o.string("code") ?: return null
    val regions = (o["regions"] as? JsonArray)?.mapNotNull { parseRegion(it) } ?: emptyList()
    val locations = (o["locations"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull } ?: emptyList()
    return PageWarning(code, o.string("message") ?: "", locations, regions)
}

private fun parseRegion(element: JsonElement): WarningRegion? {
    val o = element as? JsonObject ?: return null
    val bbox = (o["bbox"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.doubleOrNull }?.takeIf { it.size == 4 }
    val shots = (o["shots"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.intOrNull } ?: emptyList()
    val boxes = (o["shot_boxes"] as? JsonObject)?.mapNotNull { (k, v) ->
        val box = (v as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.intOrNull }
        if (box != null && box.size == 4) k to box else null
    }?.toMap() ?: emptyMap()
    return WarningRegion(bbox, o.string("location"), shots, o.string("cause"), boxes)
}

private fun JsonObject.string(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull

/** True when the page is marked with a red dot: any warning about an area (Requirement 4.3). */
fun List<PageWarning>.marksPage(): Boolean = any { it.code in PageWarning.AREA_CODES }

/** The rectangles to draw on the photo [shotId], whose decoded size is [width] x [height] pixels (Requirement 4.5). */
fun List<PageWarning>.rectsFor(shotId: String, width: Int, height: Int): List<ShotRect> {
    if (width <= 0 || height <= 0) return emptyList()
    return filter { it.code in PageWarning.AREA_CODES }.flatMap { w ->
        w.regions.mapNotNull { r ->
            val b = r.shotBoxes[shotId] ?: return@mapNotNull null
            ShotRect(
                (b[0].toFloat() / width).coerceIn(0f, 1f),
                (b[1].toFloat() / height).coerceIn(0f, 1f),
                ((b[2] + 1).toFloat() / width).coerceIn(0f, 1f),
                ((b[3] + 1).toFloat() / height).coerceIn(0f, 1f),
                w.code,
            )
        }
    }
}

/**
 * The warnings bar's lines for the photo [shotId]: each area warning's
 * message, and when none of its regions is in this photo, where to look
 * instead (Requirement 4.6). Other warnings are listed by message.
 */
fun List<PageWarning>.linesFor(shotId: String?): List<WarningLine> = mapNotNull { w ->
    if (w.message.isBlank()) return@mapNotNull null
    val inPhoto = shotId != null && w.regions.any { shotId in it.shotBoxes }
    val text = if (w.code in PageWarning.AREA_CODES && !inPhoto && w.regions.isNotEmpty()) {
        val where = w.regions.mapNotNull { it.location }.distinct()
        if (where.isEmpty()) w.message else "${w.message} Not in this photo; look at the ${where.joinToString(", ")}."
    } else w.message
    WarningLine(w.code, text)
}

/** The first of [shotIds] that one of the warnings' regions has a box in, or null (Requirement 4.4). */
fun List<PageWarning>.firstShotWithBox(shotIds: List<String>): String? {
    val boxed = filter { it.code in PageWarning.AREA_CODES }.flatMap { w -> w.regions.flatMap { it.shotBoxes.keys } }.toSet()
    return shotIds.firstOrNull { it in boxed }
}

/** The shot ids whose photo shows one of [region]'s boxes, in the order of [shotIds]. */
fun shotsShowing(region: WarningRegion, shotIds: List<String>): List<String> = shotIds.filter { it in region.shotBoxes }

internal fun JsonElement.asWarningsJson(): String = lenient.encodeToString(JsonElement.serializer(), this)

internal fun warningsJsonOf(elements: List<JsonElement>): String = JsonArray(elements).asWarningsJson()

/** Raw warnings as a JSON element, for the few places that want them unparsed. */
internal fun warningsElement(json: String): JsonElement? = try { lenient.parseToJsonElement(json) } catch (e: Exception) { null }

@Suppress("unused")
private fun JsonElement.objOrNull(): JsonObject? = try { jsonObject } catch (e: Exception) { null }

@Suppress("unused")
private fun JsonElement.arrOrNull(): JsonArray? = try { jsonArray } catch (e: Exception) { null }

@Suppress("unused")
private fun JsonElement.primOrNull(): JsonPrimitive? = try { jsonPrimitive } catch (e: Exception) { null }

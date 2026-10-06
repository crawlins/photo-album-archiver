package org.bit63.albumarchiver.data

import java.math.BigDecimal

/**
 * An album's physical page size, stored as the size string the backend's
 * `parse_page_size` reads: `letter`, `a4`, `a3`, or `<w>x<h>in` / `<w>x<h>mm`
 * (Requirement 2.4).
 *
 * US Letter is stored as `letter` rather than `8.5x11in` so that a custom
 * size the user typed as 8.5 × 11 in is still shown as custom when the
 * album is edited.
 */
sealed interface PageSize {
    val text: String

    enum class Preset(override val text: String, val label: String) : PageSize {
        LETTER("letter", "8.5 × 11 in"),
        A4("a4", "A4"),
        A3("a3", "A3"),
    }

    enum class Unit(val suffix: String, val label: String) { IN("in", "in"), MM("mm", "mm") }

    data class Custom(val width: BigDecimal, val height: BigDecimal, val unit: Unit) : PageSize {
        override val text: String get() = "${width.plain()}x${height.plain()}${unit.suffix}"
    }

    companion object {
        val DEFAULT: PageSize = Preset.LETTER

        private val customPattern = Regex("""^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(in|mm)$""")

        /** Reads a stored size string; null when it is not one the app writes. */
        fun parse(text: String): PageSize? {
            val t = text.trim().lowercase()
            Preset.entries.firstOrNull { it.text == t }?.let { return it }
            val m = customPattern.matchEntire(t) ?: return null
            val w = BigDecimal(m.groupValues[1])
            val h = BigDecimal(m.groupValues[2])
            if (w.signum() <= 0 || h.signum() <= 0) return null
            val unit = Unit.entries.first { it.suffix == m.groupValues[3] }
            return Custom(w, h, unit)
        }

        /** Human-readable form for lists, e.g. "8.5 × 11 in" or "A4". */
        fun describe(text: String): String = when (val size = parse(text)) {
            is Preset -> size.label
            is Custom -> "${size.width.plain()} × ${size.height.plain()} ${size.unit.label}"
            null -> text
        }

        private fun BigDecimal.plain(): String = stripTrailingZeros().toPlainString()
    }
}

/** A problem with one custom dimension field, shown next to it (Requirement 2.5). */
enum class DimensionError(val message: String) {
    MISSING("Required"),
    NOT_A_NUMBER("Not a number"),
    NOT_POSITIVE("Must be more than 0"),
}

object PageSizeValidation {
    /** Parses one custom width or height as typed; accepts a decimal comma. */
    fun dimension(input: String): Result {
        val t = input.trim().replace(',', '.')
        if (t.isEmpty()) return Result.Error(DimensionError.MISSING)
        val value = t.toBigDecimalOrNull() ?: return Result.Error(DimensionError.NOT_A_NUMBER)
        if (value.signum() <= 0) return Result.Error(DimensionError.NOT_POSITIVE)
        return Result.Ok(value)
    }

    sealed interface Result {
        data class Ok(val value: BigDecimal) : Result
        data class Error(val error: DimensionError) : Result
    }
}

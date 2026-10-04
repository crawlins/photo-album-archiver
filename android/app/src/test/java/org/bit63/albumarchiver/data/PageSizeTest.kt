package org.bit63.albumarchiver.data

import com.google.common.truth.Truth.assertThat
import org.junit.Test
import java.math.BigDecimal

class PageSizeTest {
    @Test fun `default is US letter stored as letter`() {
        assertThat(PageSize.DEFAULT).isEqualTo(PageSize.Preset.LETTER)
        assertThat(PageSize.DEFAULT.text).isEqualTo("letter")
    }

    @Test fun `presets format as the backend's names`() {
        assertThat(PageSize.Preset.A4.text).isEqualTo("a4")
        assertThat(PageSize.Preset.A3.text).isEqualTo("a3")
    }

    @Test fun `custom sizes format as WxH with unit and no trailing zeros`() {
        assertThat(PageSize.Custom(BigDecimal("8.50"), BigDecimal("11"), PageSize.Unit.IN).text).isEqualTo("8.5x11in")
        assertThat(PageSize.Custom(BigDecimal("254"), BigDecimal("305.0"), PageSize.Unit.MM).text).isEqualTo("254x305mm")
        assertThat(PageSize.Custom(BigDecimal("1E+1"), BigDecimal("12"), PageSize.Unit.IN).text).isEqualTo("10x12in")
    }

    @Test fun `parse round-trips everything the app writes`() {
        for (text in listOf("letter", "a4", "a3", "8.5x11in", "10x12in", "254x305mm", "0.5x0.75in")) {
            assertThat(PageSize.parse(text)!!.text).isEqualTo(text)
        }
    }

    @Test fun `parse is case and space tolerant`() {
        assertThat(PageSize.parse(" A4 ")).isEqualTo(PageSize.Preset.A4)
        assertThat(PageSize.parse("10X12IN")!!.text).isEqualTo("10x12in")
    }

    @Test fun `parse rejects what the app never writes`() {
        for (text in listOf("", "letterx", "8.5x11", "x11in", "8.5x11cm", "0x11in", "8.5x0mm", "-1x2in", "a5")) {
            assertThat(PageSize.parse(text)).isNull()
        }
    }

    @Test fun `describe gives readable labels`() {
        assertThat(PageSize.describe("letter")).isEqualTo("8.5 × 11 in")
        assertThat(PageSize.describe("a4")).isEqualTo("A4")
        assertThat(PageSize.describe("254x305mm")).isEqualTo("254 × 305 mm")
        assertThat(PageSize.describe("odd")).isEqualTo("odd")
    }

    @Test fun `dimension validation`() {
        assertThat(PageSizeValidation.dimension("")).isEqualTo(PageSizeValidation.Result.Error(DimensionError.MISSING))
        assertThat(PageSizeValidation.dimension("   ")).isEqualTo(PageSizeValidation.Result.Error(DimensionError.MISSING))
        assertThat(PageSizeValidation.dimension("abc")).isEqualTo(PageSizeValidation.Result.Error(DimensionError.NOT_A_NUMBER))
        assertThat(PageSizeValidation.dimension("1.2.3")).isEqualTo(PageSizeValidation.Result.Error(DimensionError.NOT_A_NUMBER))
        assertThat(PageSizeValidation.dimension("0")).isEqualTo(PageSizeValidation.Result.Error(DimensionError.NOT_POSITIVE))
        assertThat(PageSizeValidation.dimension("-3")).isEqualTo(PageSizeValidation.Result.Error(DimensionError.NOT_POSITIVE))
        assertThat(PageSizeValidation.dimension("8.5")).isEqualTo(PageSizeValidation.Result.Ok(BigDecimal("8.5")))
        assertThat(PageSizeValidation.dimension(" 8,5 ")).isEqualTo(PageSizeValidation.Result.Ok(BigDecimal("8.5")))
    }
}

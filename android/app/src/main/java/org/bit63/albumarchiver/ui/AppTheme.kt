package org.bit63.albumarchiver.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val Brown = Color(0xFF6D4C41)
private val Cream = Color(0xFFF5F0E6)

@Composable
fun AppTheme(content: @Composable () -> Unit) {
    val colors = if (isSystemInDarkTheme()) {
        darkColorScheme(primary = Color(0xFFD7B9A6), secondary = Color(0xFFBCAAA4))
    } else {
        lightColorScheme(primary = Brown, secondary = Color(0xFF8D6E63), background = Cream)
    }
    MaterialTheme(colorScheme = colors, content = content)
}

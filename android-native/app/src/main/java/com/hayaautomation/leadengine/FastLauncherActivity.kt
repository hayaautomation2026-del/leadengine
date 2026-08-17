package com.hayaautomation.leadengine

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

private const val FAST_URL = "https://jequjltbvofmhfnbsfzt.supabase.co"
private const val FAST_EMAIL = "hayaautomation2026@gmail.com"
private const val FAST_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImplcXVqbHRidm9mbWhmbmJzZnp0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI3NjM3MDYsImV4cCI6MjA5ODMzOTcwNn0.swI7ZldCu4W0fyFNjiOoBMkINEKBfozAVnVJjXj_6VM"

class FastLauncherActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences("leadengine_secure", Context.MODE_PRIVATE)
        if (prefs.contains("refresh_token")) {
            openMain()
            return
        }
        setContent { FastLogin { openMain() } }
    }

    private fun openMain() {
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }
}

@Composable
private fun FastLogin(done: () -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var password by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val bg = Color(0xFF06100E)
    val panel = Color(0xFF0D1C18)
    val green = Color(0xFF48FFA2)
    val text = Color(0xFFF4FFF9)
    val red = Color(0xFFFF5F7D)

    MaterialTheme(colorScheme = darkColorScheme(background = bg, surface = panel, primary = green)) {
        Box(Modifier.fillMaxSize().background(bg), contentAlignment = Alignment.Center) {
            Card(
                Modifier.fillMaxWidth().padding(20.dp),
                colors = CardDefaults.cardColors(containerColor = panel),
                shape = RoundedCornerShape(24.dp)
            ) {
                Column(Modifier.padding(20.dp)) {
                    Text("⚡ LeadEngine", color = green, fontWeight = FontWeight.Black, fontSize = 28.sp)
                    Spacer(Modifier.height(12.dp))
                    Text("Password", color = text, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it; error = null },
                        label = { Text("Enter password") },
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    error?.let { Text(it, color = red, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp)) }
                    Spacer(Modifier.height(14.dp))
                    Button(
                        onClick = {
                            if (password.isBlank()) {
                                error = "Enter your password."
                            } else {
                                busy = true
                                scope.launch {
                                    runCatching {
                                        val refresh = FastAuth.login(password)
                                        FastStore.save(context, refresh)
                                    }.onSuccess { done() }
                                     .onFailure { error = "Wrong password or connection failed." }
                                    busy = false
                                }
                            }
                        },
                        enabled = !busy,
                        modifier = Modifier.fillMaxWidth().height(54.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = green, contentColor = bg)
                    ) {
                        Text(if (busy) "Opening…" else "Open", fontWeight = FontWeight.Black)
                    }
                }
            }
        }
    }
}

private object FastAuth {
    suspend fun login(password: String): String = withContext(Dispatchers.IO) {
        val conn = (URL("$FAST_URL/auth/v1/token?grant_type=password").openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 8000
            readTimeout = 12000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("apikey", FAST_KEY)
        }
        try {
            val body = JSONObject().put("email", FAST_EMAIL).put("password", password).toString()
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val raw = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw IllegalStateException("Auth failed")
            JSONObject(raw).getString("refresh_token")
        } finally {
            conn.disconnect()
        }
    }
}

private object FastStore {
    private const val PREFS = "leadengine_secure"
    private const val KEY_ALIAS = "leadengine_session_key"

    fun save(context: Context, refreshToken: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("refresh_token", encrypt(refreshToken))
            .putBoolean("password_changed", true)
            .apply()
    }

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return generator.generateKey()
    }

    private fun encrypt(value: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        return Base64.encodeToString(cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
    }
}

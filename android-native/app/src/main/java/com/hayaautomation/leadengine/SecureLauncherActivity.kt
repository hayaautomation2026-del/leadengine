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

private const val LE_SUPABASE_URL = "https://jequjltbvofmhfnbsfzt.supabase.co"
private const val LE_OWNER_EMAIL = "hayaautomation2026@gmail.com"
private const val LE_PUBLIC_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImplcXVqbHRidm9mbWhmbmJzZnp0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI3NjM3MDYsImV4cCI6MjA5ODMzOTcwNn0.swI7ZldCu4W0fyFNjiOoBMkINEKBfozAVnVJjXj_6VM"

private val LE_BG = Color(0xFF06100E)
private val LE_PANEL = Color(0xFF0D1C18)
private val LE_GREEN = Color(0xFF48FFA2)
private val LE_TEXT = Color(0xFFF4FFF9)
private val LE_MUTED = Color(0xFF829E94)
private val LE_RED = Color(0xFFFF5F7D)

class SecureLauncherActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences("leadengine_secure", Context.MODE_PRIVATE)
        if (prefs.getBoolean("password_changed", false) && prefs.contains("refresh_token")) {
            openMain()
            return
        }
        setContent { SecureSetupScreen { openMain() } }
    }

    private fun openMain() {
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }
}

private enum class SetupStep { LOGIN, CHANGE }

@Composable
private fun SecureSetupScreen(done: () -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var step by remember { mutableStateOf(SetupStep.LOGIN) }
    var loginPassword by remember { mutableStateOf("") }
    var first by remember { mutableStateOf("") }
    var second by remember { mutableStateOf("") }
    var accessToken by remember { mutableStateOf<String?>(null) }
    var refreshToken by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    MaterialTheme(colorScheme = darkColorScheme(background = LE_BG, surface = LE_PANEL, primary = LE_GREEN)) {
        Box(Modifier.fillMaxSize().background(LE_BG), contentAlignment = Alignment.Center) {
            Card(
                Modifier.fillMaxWidth().padding(20.dp),
                colors = CardDefaults.cardColors(containerColor = LE_PANEL),
                shape = RoundedCornerShape(24.dp)
            ) {
                Column(Modifier.padding(20.dp)) {
                    Text("⚡ LeadEngine", color = LE_GREEN, fontWeight = FontWeight.Black, fontSize = 28.sp)
                    Spacer(Modifier.height(8.dp))

                    if (step == SetupStep.LOGIN) {
                        Text("Owner sign-in", color = LE_TEXT, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        Text("Sign in once, then create your private password.", color = LE_MUTED, fontSize = 11.sp)
                        Spacer(Modifier.height(16.dp))
                        OutlinedTextField(
                            value = LE_OWNER_EMAIL,
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("Owner") },
                            modifier = Modifier.fillMaxWidth()
                        )
                        Spacer(Modifier.height(10.dp))
                        OutlinedTextField(
                            value = loginPassword,
                            onValueChange = { loginPassword = it; error = null },
                            label = { Text("Password") },
                            visualTransformation = PasswordVisualTransformation(),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        error?.let { Text(it, color = LE_RED, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp)) }
                        Spacer(Modifier.height(14.dp))
                        Button(
                            onClick = {
                                if (loginPassword.isBlank()) { error = "Enter the temporary password."; return@Button }
                                busy = true
                                scope.launch {
                                    runCatching { SecureSetupApi.login(loginPassword) }
                                        .onSuccess {
                                            accessToken = it.first
                                            refreshToken = it.second
                                            step = SetupStep.CHANGE
                                            error = null
                                        }
                                        .onFailure { error = "Login failed. Check the password and try again." }
                                    busy = false
                                }
                            },
                            enabled = !busy,
                            modifier = Modifier.fillMaxWidth().height(54.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = LE_GREEN, contentColor = LE_BG)
                        ) { Text(if (busy) "Signing in…" else "Continue", fontWeight = FontWeight.Black) }
                    } else {
                        Text("Create your private password", color = LE_TEXT, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        Text("At least 12 characters. Both boxes must match.", color = LE_MUTED, fontSize = 11.sp)
                        Spacer(Modifier.height(16.dp))
                        OutlinedTextField(
                            value = first,
                            onValueChange = { first = it; error = null },
                            label = { Text("New password") },
                            visualTransformation = PasswordVisualTransformation(),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        Text("${first.length}/12 characters", color = if (first.length >= 12) LE_GREEN else LE_MUTED, fontSize = 11.sp)
                        Spacer(Modifier.height(10.dp))
                        OutlinedTextField(
                            value = second,
                            onValueChange = { second = it; error = null },
                            label = { Text("Repeat password") },
                            visualTransformation = PasswordVisualTransformation(),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        if (second.isNotEmpty()) {
                            Text(
                                if (first == second) "Passwords match ✓" else "Passwords do not match",
                                color = if (first == second) LE_GREEN else LE_RED,
                                fontSize = 11.sp
                            )
                        }
                        error?.let { Text(it, color = LE_RED, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp)) }
                        Spacer(Modifier.height(14.dp))
                        Button(
                            onClick = {
                                when {
                                    first.length < 12 -> error = "Password must be at least 12 characters."
                                    first != second -> error = "The two passwords are different."
                                    accessToken == null || refreshToken == null -> error = "Session expired. Reopen the app and sign in again."
                                    else -> {
                                        busy = true
                                        scope.launch {
                                            runCatching {
                                                SecureSetupApi.changePassword(accessToken!!, first)
                                                SecureSetupStore.save(context, refreshToken!!)
                                            }.onSuccess { done() }
                                             .onFailure { error = "Could not save password. Try again." }
                                            busy = false
                                        }
                                    }
                                }
                            },
                            enabled = !busy,
                            modifier = Modifier.fillMaxWidth().height(54.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = LE_GREEN, contentColor = LE_BG)
                        ) { Text(if (busy) "Saving…" else "Save private password", fontWeight = FontWeight.Black) }
                    }
                }
            }
        }
    }
}

private object SecureSetupApi {
    suspend fun login(password: String): Pair<String, String> = withContext(Dispatchers.IO) {
        val body = JSONObject().put("email", LE_OWNER_EMAIL).put("password", password)
        val json = request("$LE_SUPABASE_URL/auth/v1/token?grant_type=password", "POST", body, null)
        json.getString("access_token") to json.getString("refresh_token")
    }

    suspend fun changePassword(token: String, password: String) = withContext(Dispatchers.IO) {
        request("$LE_SUPABASE_URL/auth/v1/user", "PUT", JSONObject().put("password", password), token)
    }

    private fun request(url: String, method: String, body: JSONObject, bearer: String?): JSONObject {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15000
            readTimeout = 30000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("apikey", LE_PUBLIC_KEY)
            if (bearer != null) setRequestProperty("Authorization", "Bearer $bearer")
        }
        try {
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw IllegalStateException("Auth failed $code")
            return JSONObject(text)
        } finally { conn.disconnect() }
    }
}

private object SecureSetupStore {
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

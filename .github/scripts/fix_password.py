from pathlib import Path

p = Path('android-native/app/src/main/java/com/hayaautomation/leadengine/MainActivity.kt')
s = p.read_text()
old = '''@Composable
private fun ChangePasswordScreen(error: String?, onChange: (String) -> Unit) {
    var first by remember { mutableStateOf("") }
    var second by remember { mutableStateOf("") }
    val valid = first.length >= 12 && first == second
    Box(Modifier.fillMaxSize().background(Bg), contentAlignment = Alignment.Center) {
        Card(Modifier.fillMaxWidth().padding(20.dp), colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(24.dp)) {
            Column(Modifier.padding(20.dp)) {
                Text("Create your private password", color = Text, fontWeight = FontWeight.Black, fontSize = 22.sp)
                Text("Do this once. Use at least 12 characters.", color = Muted, fontSize = 11.sp)
                Spacer(Modifier.height(16.dp))
                PasswordField("New password", first) { first = it }
                Spacer(Modifier.height(10.dp))
                PasswordField("Repeat password", second) { second = it }
                if (second.isNotEmpty() && first != second) Text("Passwords do not match", color = ErrorRed, fontSize = 11.sp)
                error?.let { Text(it, color = ErrorRed, fontSize = 11.sp) }
                Spacer(Modifier.height(14.dp))
                Button(onClick = { onChange(first) }, enabled = valid, modifier = Modifier.fillMaxWidth(), colors = ButtonDefaults.buttonColors(containerColor = Green, contentColor = Bg)) {
                    Text("Save private password", fontWeight = FontWeight.Black)
                }
            }
        }
    }
}
'''
new = '''@Composable
private fun ChangePasswordScreen(error: String?, onChange: (String) -> Unit) {
    var first by remember { mutableStateOf("") }
    var second by remember { mutableStateOf("") }
    var localError by remember { mutableStateOf<String?>(null) }
    val matches = first == second && second.isNotEmpty()
    Box(Modifier.fillMaxSize().background(Bg), contentAlignment = Alignment.Center) {
        Card(Modifier.fillMaxWidth().padding(20.dp), colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(24.dp)) {
            Column(Modifier.padding(20.dp)) {
                Text("Create your private password", color = Text, fontWeight = FontWeight.Black, fontSize = 22.sp)
                Text("Use at least 12 characters. Both boxes must be identical.", color = Muted, fontSize = 11.sp)
                Spacer(Modifier.height(16.dp))
                PasswordField("New password", first) { first = it; localError = null }
                Text("${first.length}/12 characters", color = if (first.length >= 12) Green else Muted, fontSize = 11.sp)
                Spacer(Modifier.height(10.dp))
                PasswordField("Repeat password", second) { second = it; localError = null }
                if (second.isNotEmpty()) Text(if (matches) "Passwords match ✓" else "Passwords do not match", color = if (matches) Green else ErrorRed, fontSize = 11.sp)
                localError?.let { Text(it, color = ErrorRed, fontSize = 11.sp) }
                error?.let { Text(it, color = ErrorRed, fontSize = 11.sp) }
                Spacer(Modifier.height(14.dp))
                Button(
                    onClick = {
                        when {
                            first.length < 12 -> localError = "Password must be at least 12 characters."
                            first != second -> localError = "The two passwords are different."
                            else -> { localError = null; onChange(first) }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Green, contentColor = Bg)
                ) { Text("Save private password", fontWeight = FontWeight.Black) }
            }
        }
    }
}
'''
if old not in s:
    raise SystemExit('password block not found')
p.write_text(s.replace(old, new))

const $ = (id) => document.getElementById(id);
let setupRequired = false;

async function request(path, options = {}) {
  const response = await fetch(path, { ...options, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `请求失败 (${response.status})`);
  return data;
}

async function boot() {
  try {
    const status = await request("/api/auth/status");
    if (status.authenticated) {
      const next = new URLSearchParams(location.search).get("next");
      location.replace(next && next.startsWith("/") ? next : "/");
      return;
    }
    setupRequired = Boolean(status.setup_required);
    if (setupRequired) {
      $("title").textContent = "创建管理员";
      $("intro").textContent = "首次运行：创建用于管理服务器和连接手机的管理员账号。";
      $("submitBtn").textContent = "创建并进入";
      $("password").autocomplete = "new-password";
      $("confirmLabel").hidden = false;
      $("confirmPassword").required = true;
    }
  } catch (error) {
    $("status").textContent = error.message;
  }
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("submitBtn").disabled = true;
  $("status").textContent = "";
  try {
    if (setupRequired && $("password").value !== $("confirmPassword").value) throw new Error("两次输入的密码不一致。");
    await request(setupRequired ? "/api/auth/setup" : "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("username").value.trim(), password: $("password").value }),
    });
    const next = new URLSearchParams(location.search).get("next");
    location.replace(next && next.startsWith("/") ? next : "/");
  } catch (error) {
    $("status").textContent = error.message;
    $("submitBtn").disabled = false;
  }
});

boot();

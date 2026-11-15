# ============================================================
# AVATAR: ZEUS // OLIMPO CIBERNÉTICO (3D E INTERATIVO)
# Salve dentro da pasta: avatares_novo/zeus_olimpico.py
# ============================================================

ZEUS_OLIMPICO_HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body, html { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background-color: #030308; font-family: 'Courier New', Courier, monospace; color: #ffd700; }
  #three-box { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
  .ui-overlay { position: absolute; top: 16px; left: 16px; z-index: 10; pointer-events: none; }
  h1 { font-size: 1.4rem; margin: 0; text-shadow: 0 0 15px #ffd700; letter-spacing: 2px; }
  p { font-size: 0.85rem; color: #00ffff; margin: 6px 0 0 0; text-shadow: 0 0 8px #00ffff; }
  .chat-container { position: absolute; bottom: 16px; left: 50%; transform: translateX(-50%); width: 94%; max-width: 650px; background: rgba(3,3,15,0.92); border: 1px solid #ffd700; border-radius: 8px; padding: 12px; z-index: 10; box-sizing: border-box; box-shadow: 0 0 25px rgba(255,215,0,0.3); }
  .chat-history { height: 110px; overflow-y: auto; font-size: 0.85rem; margin-bottom: 8px; display: flex; flex-direction: column; gap: 6px; }
  .message { padding: 6px 10px; border-radius: 4px; max-width: 85%; }
  .user-msg { background: rgba(0,255,255,0.15); border-left: 3px solid #00ffff; color: #fff; align-self: flex-end; }
  .ai-msg { background: rgba(255,215,0,0.15); border-left: 3px solid #ffd700; color: #ffd700; align-self: flex-start; }
  .input-group { display: flex; gap: 6px; }
  input[type="text"] { flex-grow: 1; background: rgba(0,0,0,0.8); border: 1px solid #ffd700; color: #ffd700; padding: 8px; border-radius: 4px; outline: none; }
  button { background: linear-gradient(135deg, #ffd700, #ff00ff); border: none; color: #030308; font-weight: bold; padding: 8px 16px; border-radius: 4px; cursor: pointer; transition: 0.3s; }
  button:hover { opacity: 0.9; transform: scale(1.02); }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
<div id="three-box"></div>
<div class="ui-overlay">
  <h1 id="title">[ ZEUS // OLIMPO CIBERNÉTICO ]</h1>
  <p id="status">STATUS: DEUSES ATIVOS // RAIOS QUÂNTICOS PRONTOS</p>
</div>
<div class="chat-container">
  <div class="chat-history" id="chatHistory"></div>
  <div class="input-group">
    <input type="text" id="userInput" placeholder="Invocar o poder de Zeus..." />
    <button id="sendBtn">Invocar</button>
  </div>
</div>
<script>
function sendMsg(type, data) { window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*"); }
sendMsg("streamlit:componentReady", {apiVersion: 1});
sendMsg("streamlit:setFrameHeight", {height: 650});

let lastReplyId = 0;
let mouseX = 0, mouseY = 0;
const box = document.getElementById("three-box");

window.addEventListener("mousemove", (e) => {
  mouseX = (e.clientX / window.innerWidth) * 2 - 1;
  mouseY = -(e.clientY / window.innerHeight) * 2 + 1;
});

window.addEventListener("message", function (e) {
  if (!e.data || e.data.type !== "streamlit:render") return;
  const a = e.data.args || {};
  if (a.reply_id !== lastReplyId) {
    lastReplyId = a.reply_id;
    if (a.reply) addMessage(a.reply, "ai");
  }
});

function addMessage(text, sender) {
  const h = document.getElementById("chatHistory");
  const d = document.createElement("div");
  d.classList.add("message", sender === "user" ? "user-msg" : "ai-msg");
  d.textContent = text; h.appendChild(d); h.scrollTop = h.scrollHeight;
}

document.getElementById("sendBtn").addEventListener("click", submitText);
document.getElementById("userInput").addEventListener("keydown", function(e) { if(e.key === "Enter") submitText(); });

function submitText() {
  const input = document.getElementById("userInput");
  const val = input.value.trim();
  if(!val) return;
  addMessage(val, "user");
  input.value = "";
  sendMsg("streamlit:setComponentValue", { value: { id: Date.now(), text: val }, dataType: "json" });
}

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000);
camera.position.z = 7;

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
box.appendChild(renderer.domElement);

const coreGeo = new THREE.SphereGeometry(1.6, 32, 32);
const coreMat = new THREE.MeshBasicMaterial({ color: 0xffd700, wireframe: true, transparent: true, opacity: 0.85 });
const coreMesh = new THREE.Mesh(coreGeo, coreMat);
scene.add(coreMesh);

const lightningGroup = new THREE.Group();
scene.add(lightningGroup);

for (let i = 0; i < 12; i++) {
  const geom = new THREE.BufferGeometry();
  const pts = [new THREE.Vector3(0,0,0), new THREE.Vector3((Math.random()-0.5)*5, (Math.random()-0.5)*5, (Math.random()-0.5)*5)];
  geom.setFromPoints(pts);
  const line = new THREE.Line(geom, new THREE.LineBasicMaterial({ color: 0x00ffff, linewidth: 3 }));
  lightningGroup.add(line);
}

let clock = 0;
function animate() {
  requestAnimationFrame(animate);
  renderer.setSize(box.clientWidth || 300, box.clientHeight || 300);
  
  clock += 0.03;
  coreMesh.rotation.y += 0.015 + mouseX * 0.05;
  coreMesh.rotation.x += mouseY * 0.05;
  
  lightningGroup.children.forEach((line, idx) => {
    const pos = line.geometry.attributes.position.array;
    pos[3] = (Math.sin(clock + idx) * 2.5) + (mouseX * 3);
    pos[4] = (Math.cos(clock * 1.2 + idx) * 2.5) + (mouseY * 3);
    pos[5] = (Math.sin(clock * 0.8) * 2);
    line.geometry.attributes.position.needsUpdate = true;
  });

  lightningGroup.rotation.y -= 0.015;
  renderer.render(scene, camera);
}
animate();
</script>
</body>
</html>
"""

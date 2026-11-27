(function () {
  "use strict";

  function ready(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback);
    } else {
      callback();
    }
  }

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function initSidebar() {
    var button = document.getElementById("menuButton");
    var sidebar = document.getElementById("sidebar");
    var backdrop = document.getElementById("sidebarBackdrop");
    if (!button || !sidebar || !backdrop) return;

    function close() {
      sidebar.classList.remove("open");
      backdrop.classList.remove("show");
    }
    function setExpanded(expanded) {
      button.setAttribute("aria-expanded", expanded ? "true" : "false");
    }
    button.addEventListener("click", function () {
      var open = sidebar.classList.toggle("open");
      backdrop.classList.toggle("show", open);
      setExpanded(open);
    });
    backdrop.addEventListener("click", function () {
      close();
      setExpanded(false);
    });
    sidebar.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        close();
        setExpanded(false);
      });
    });
  }

  function initAnimations() {
    if (!window.gsap || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    window.gsap.from(".kpi", { opacity: 0, y: 16, duration: 0.55, stagger: 0.08, ease: "power2.out" });
    window.gsap.from(".card", { opacity: 0, y: 12, duration: 0.6, stagger: 0.06, ease: "power2.out", delay: 0.1 });
  }

  function hexColor(value) {
    return value === "failed" ? "#ff6b7a" : value === "success" ? "#d4af37" : "#5aa9ff";
  }

  function initCharts() {
    if (!window.Chart) return;
    var defaults = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#91a0bd", boxWidth: 10 } } },
      scales: {
        x: { ticks: { color: "#91a0bd" }, grid: { color: "rgba(255,255,255,0.05)" } },
        y: { ticks: { color: "#91a0bd", precision: 0 }, grid: { color: "rgba(255,255,255,0.05)" }, beginAtZero: true }
      }
    };
    document.querySelectorAll("[data-chart]").forEach(function (canvas) {
      var raw = canvas.getAttribute("data-chart-data") || "{}";
      var data;
      try { data = JSON.parse(raw); } catch (error) { return; }
      var kind = canvas.getAttribute("data-chart");
      var config;
      if (kind === "activity") {
        config = {
          type: "line",
          data: {
            labels: data.labels || [],
            datasets: [
              { label: "Successful", data: data.success || [], borderColor: "#d4af37", backgroundColor: "rgba(212,175,55,0.12)", fill: true, tension: 0.38 },
              { label: "Failed", data: data.failed || [], borderColor: "#ff6b7a", backgroundColor: "rgba(255,107,122,0.08)", fill: true, tension: 0.38 }
            ]
          },
          options: defaults
        };
      } else {
        config = {
          type: "doughnut",
          data: {
            labels: data.labels || [],
            datasets: [{ data: data.values || [], backgroundColor: ["#d4af37", "#5aa9ff", "#a78bfa", "#ff6b7a"], borderWidth: 0 }]
          },
          options: { responsive: true, maintainAspectRatio: false, cutout: "72%", plugins: { legend: { position: "bottom", labels: { color: "#91a0bd", boxWidth: 10 } } } }
        };
      }
      new window.Chart(canvas, config);
    });
  }

  function initThree() {
    var container = document.getElementById("workflow3d");
    if (!container || !window.THREE) return;
    var raw = container.getAttribute("data-workflow-3d") || "[]";
    var nodes;
    try { nodes = JSON.parse(raw); } catch (error) { nodes = []; }
    if (!nodes.length) return;

    var scene = new window.THREE.Scene();
    var camera = new window.THREE.PerspectiveCamera(52, container.clientWidth / container.clientHeight, 0.1, 2000);
    camera.position.set(0, 80, 300);
    var renderer = new window.THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    scene.add(new window.THREE.AmbientLight(0xffffff, 0.7));
    var light = new window.THREE.PointLight(0xd4af37, 2.2);
    light.position.set(80, 120, 180);
    scene.add(light);

    var points = [];
    nodes.forEach(function (node, index) {
      var position = node.position || { x: 0, y: 0 };
      var point = new window.THREE.Vector3(
        (position.x - 400) / 9,
        (position.y - 200) / 14,
        Math.sin(index) * 26
      );
      points.push(point);
      var geometry = new window.THREE.SphereGeometry(8, 24, 24);
      var color = node.type === "condition" ? 0xa78bfa : node.type === "trigger" ? 0xd4af37 : 0x5aa9ff;
      var material = new window.THREE.MeshStandardMaterial({ color: color, metalness: 0.6, roughness: 0.25 });
      var sphere = new window.THREE.Mesh(geometry, material);
      sphere.position.copy(point);
      scene.add(sphere);
    });

    var lineMaterial = new window.THREE.LineBasicMaterial({ color: 0x6f7f9f, transparent: true, opacity: 0.5 });
    for (var i = 0; i < points.length - 1; i += 1) {
      var geometry = new window.THREE.BufferGeometry().setFromPoints([points[i], points[i + 1]]);
      scene.add(new window.THREE.Line(geometry, lineMaterial));
    }

    var frame = 0;
    function animate() {
      frame += 0.004;
      scene.rotation.y = Math.sin(frame) * 0.22;
      scene.rotation.x = Math.sin(frame * 0.6) * 0.06;
      renderer.render(scene, camera);
      window.requestAnimationFrame(animate);
    }
    animate();

    window.addEventListener("resize", function () {
      if (!container.clientWidth) return;
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    });
  }

  window.NexaFlow = { csrfToken: csrfToken, hexColor: hexColor };

  ready(function () {
    initSidebar();
    initAnimations();
    initCharts();
    initThree();
  });
})();

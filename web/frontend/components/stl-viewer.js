"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

export default function StlViewer({ url }) {
  const host = useRef(null);
  const [state, setState] = useState("loading");

  useEffect(() => {
    const element = host.current;
    const abort = new AbortController();
    let active = true;
    let renderer, controls, observer, geometry, material, edgeGeometry, edgeMaterial;
    const cleanup = () => {
      active = false;
      abort.abort();
      observer?.disconnect();
      controls?.dispose();
      geometry?.dispose();
      material?.dispose();
      edgeGeometry?.dispose();
      edgeMaterial?.dispose();
      renderer?.dispose();
      renderer?.domElement.remove();
    };

    async function load() {
      try {
        setState("loading");
        const response = await fetch(url, { signal: abort.signal, cache: "no-store" });
        if (!response.ok) throw new Error("STL could not be downloaded.");
        const bytes = await response.arrayBuffer();
        if (!active) return;
        geometry = new STLLoader().parse(bytes);
        geometry.center();
        geometry.computeBoundingSphere();
        const radius = geometry.boundingSphere?.radius;
        if (!Number.isFinite(radius) || radius <= 0 || !geometry.getAttribute("position")?.count) {
          throw new Error("STL contains no displayable geometry.");
        }
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.domElement.setAttribute("aria-label", "生成的 CAD 模型：拖动旋转，滚轮缩放");
        renderer.domElement.setAttribute("role", "img");
        element.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        scene.background = new THREE.Color("#f7fafb");
        const camera = new THREE.PerspectiveCamera(40, 1, radius / 1000, radius * 1000);
        camera.up.set(0, 0, 1);
        material = new THREE.MeshStandardMaterial({ color: "#168892", roughness: 0.48, metalness: 0.18 });
        scene.add(new THREE.Mesh(geometry, material));
        edgeGeometry = new THREE.EdgesGeometry(geometry, 30);
        edgeMaterial = new THREE.LineBasicMaterial({ color: "#225a66", transparent: true, opacity: 0.45 });
        scene.add(new THREE.LineSegments(edgeGeometry, edgeMaterial));
        scene.add(new THREE.HemisphereLight(0xffffff, 0x9eafbb, 2.5));
        const light = new THREE.DirectionalLight(0xffffff, 3);
        light.position.set(2, -4, 5);
        scene.add(light);
        controls = new OrbitControls(camera, renderer.domElement);
        controls.target.set(0, 0, 0);
        controls.minDistance = radius * 1.1;
        controls.maxDistance = radius * 30;
        // Render only on interaction/resize, with no permanent animation loop.
        const render = () => renderer.render(scene, camera);
        controls.addEventListener("change", render);
        const fit = () => {
          const width = Math.max(1, element.clientWidth);
          const height = Math.max(1, element.clientHeight);
          renderer.setSize(width, height, false);
          camera.aspect = width / height;
          const vertical = THREE.MathUtils.degToRad(camera.fov);
          const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * camera.aspect);
          const distance = 1.2 * radius / Math.sin(Math.min(vertical, horizontal) / 2);
          camera.position.set(1, -1.4, 1.2).normalize().multiplyScalar(distance);
          camera.updateProjectionMatrix();
          controls.update();
          render();
        };
        observer = new ResizeObserver(fit);
        observer.observe(element);
        fit();
        setState("ready");
      } catch (error) {
        if (!active) return;
        setState(error.message || "WebGL preview is unavailable. Download the STL to inspect it.");
        cleanup();
      }
    }
    load();
    return cleanup;
  }, [url]);

  return (
    <div className="stl-viewer" ref={host}>
      {state === "loading" && <span className="viewer-message" role="status">加载 3D 模型...</span>}
      {state !== "loading" && state !== "ready" && <span className="viewer-message viewer-error" role="alert">3D 预览不可用：{state} 可保存 STL 后查看。</span>}
    </div>
  );
}

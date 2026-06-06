

const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    url: 'https://live.kuaishou.com',
    pretendToBeVisual: true,
    resources: "usable"
});

// 获取全局对象
window = dom.window;
document = window.document;
navigator = window.navigator;
location = window.location;
history = window.history;
screen = window.screen;

// 修复 navigator.sendBeacon
if (!navigator.sendBeacon) {
    navigator.sendBeacon = function(url, data) {
        return true;
    };
}

// 确保console.err存在（如果有代码使用它）
if (console && !console.err) {
    console.err = console.error;
}


// 添加其他可能缺失的API
if (!window.requestAnimationFrame) {
    window.requestAnimationFrame = function(callback) {
        return setTimeout(() => callback(Date.now()), 16);
    };
}

if (!window.cancelAnimationFrame) {
    window.cancelAnimationFrame = function(id) {
        clearTimeout(id);
    };
}

// 添加Performance API
if (!window.performance) {
    window.performance = {
        now: () => Date.now(),
        timing: {
            navigationStart: Date.now()
        }
    };
}

// 添加localStorage和sessionStorage
if (!window.localStorage) {
    window.localStorage = {
        getItem: () => null,
        setItem: () => {},
        removeItem: () => {},
        clear: () => {},
        length: 0
    };
}

if (!window.sessionStorage) {
    window.sessionStorage = {
        getItem: () => null,
        setItem: () => {},
        removeItem: () => {},
        clear: () => {},
        length: 0
    };
}

// 添加XMLHttpRequest（如果需要）
if (!window.XMLHttpRequest) {
    window.XMLHttpRequest = function() {
        return {
            open: () => {},
            send: () => {},
            setRequestHeader: () => {},
            readyState: 4,
            status: 200,
            responseText: '{}'
        };
    };
}

// 修复Canvas
window.HTMLCanvasElement.prototype.getContext = () => {
    return {
        fillRect: () => {},
        clearRect: () => {},
        getImageData: (x, y, w, h) => ({
            data: new Array(w * h * 4).fill(0)
        }),
        putImageData: () => {},
        createImageData: (w, h) => ({
            data: new Array(w * h * 4).fill(0)
        }),
        setTransform: () => {},
        drawImage: () => {},
        save: () => {},
        restore: () => {},
        translate: () => {},
        rotate: () => {},
        scale: () => {},
        transform: () => {},
        resetTransform: () => {},
        // WebGL相关方法
        createBuffer: () => ({}),
        createProgram: () => ({}),
        createShader: () => ({}),
        getSupportedExtensions: () => [],
        getExtension: () => ({}),
        getParameter: () => 0,
        getContextAttributes: () => ({}),
        getShaderPrecisionFormat: () => ({
            rangeMin: 127,
            rangeMax: 127,
            precision: 23
        })
    };
};

// 添加必要的Event相关API
if (!window.MouseEvent) {
    window.MouseEvent = class MouseEvent {
        constructor(type, init) {
            this.type = type;
            this.clientX = init?.clientX || 0;
            this.clientY = init?.clientY || 0;
        }
    };
}

// 添加URL API（如果需要）
if (!window.URL) {
    window.URL = {
        createObjectURL: () => 'blob:null',
        revokeObjectURL: () => {}
    };
}

// 添加Blob API（如果需要）
if (!window.Blob) {
    window.Blob = class Blob {
        constructor(parts, options) {
            this.parts = parts;
            this.type = options?.type || '';
        }
    };
}

// 添加atob和btoa（如果需要）
if (!window.atob) {
    window.atob = function(str) {
        return Buffer.from(str, 'base64').toString('binary');
    };
}

if (!window.btoa) {
    window.btoa = function(str) {
        return Buffer.from(str, 'binary').toString('base64');
    };
}

// 添加其他可能需要的全局对象
window.self = window;
self = window
window.top = window;
window.parent = window;
window.frames = window;

// 添加requestIdleCallback（如果需要）
if (!window.requestIdleCallback) {
    window.requestIdleCallback = function(callback) {
        return setTimeout(() => callback({
            didTimeout: false,
            timeRemaining: () => 50
        }), 1);
    };
}

if (!window.cancelIdleCallback) {
    window.cancelIdleCallback = function(id) {
        clearTimeout(id);
    };
}

// 添加IntersectionObserver（如果需要）
if (!window.IntersectionObserver) {
    window.IntersectionObserver = class IntersectionObserver {
        constructor() {}
        observe() {}
        unobserve() {}
        disconnect() {}
    };
}

// 添加MatchMedia（如果需要）
if (!window.matchMedia) {
    window.matchMedia = function() {
        return {
            matches: false,
            addListener: () => {},
            removeListener: () => {}
        };
    };
}

// 添加必要的CSS相关对象
if (!window.CSS) {
    window.CSS = {
        supports: () => false
    };
}

// 添加DeviceOrientationEvent（如果需要）
if (!window.DeviceOrientationEvent) {
    window.DeviceOrientationEvent = class DeviceOrientationEvent {
        constructor() {}
    };
}

// 添加DeviceMotionEvent（如果需要）
if (!window.DeviceMotionEvent) {
    window.DeviceMotionEvent = class DeviceMotionEvent {
        constructor() {}
    };
}

// 添加VisualViewport（如果需要）
if (!window.VisualViewport) {
    window.VisualViewport = {
        width: 1024,
        height: 768,
        scale: 1,
        offsetTop: 0,
        offsetLeft: 0,
        pageTop: 0,
        pageLeft: 0,
        addEventListener: () => {},
        removeEventListener: () => {}
    };
}

// 添加PerformanceObserver（如果需要）
if (!window.PerformanceObserver) {
    window.PerformanceObserver = class PerformanceObserver {
        constructor() {}
        observe() {}
        disconnect() {}
        takeRecords() { return []; }
    };
}

// 添加ReportingObserver（如果需要）
if (!window.ReportingObserver) {
    window.ReportingObserver = class ReportingObserver {
        constructor() {}
        observe() {}
        disconnect() {}
        takeRecords() { return []; }
    };
}

// 添加ResizeObserver（如果需要）
if (!window.ResizeObserver) {
    window.ResizeObserver = class ResizeObserver {
        constructor() {}
        observe() {}
        unobserve() {}
        disconnect() {}
    };
}

// 添加Intl（如果需要）
if (!window.Intl) {
    window.Intl = {
        DateTimeFormat: class DateTimeFormat {
            format() { return new Date().toString(); }
        },
        NumberFormat: class NumberFormat {
            format() { return '0'; }
        }
    };
}

// 添加必要的Web API
if (!window.TextEncoder) {
    window.TextEncoder = class TextEncoder {
        encode(str) {
            return new Uint8Array(Buffer.from(str));
        }
    };
}

if (!window.TextDecoder) {
    window.TextDecoder = class TextDecoder {
        decode(buffer) {
            return Buffer.from(buffer).toString();
        }
    };
}

// 添加crypto（如果需要）
if (!window.crypto) {
    window.crypto = {
        getRandomValues: function(array) {
            for (let i = 0; i < array.length; i++) {
                array[i] = Math.floor(Math.random() * 256);
            }
            return array;
        },
        subtle: {
            digest: () => Promise.resolve(new ArrayBuffer(0))
        }
    };
}

// 添加Notification（如果需要）
if (!window.Notification) {
    window.Notification = {
        permission: 'denied',
        requestPermission: () => Promise.resolve('denied')
    };
}

// 添加Permissions（如果需要）
if (!window.Permissions) {
    window.Permissions = {
        query: () => Promise.resolve({ state: 'denied' })
    };
}

// 添加Navigator相关属性
if (!navigator.mediaDevices) {
    navigator.mediaDevices = {
        getUserMedia: () => Promise.reject(new Error('Not implemented')),
        enumerateDevices: () => Promise.resolve([])
    };
}

// 添加Gamepad API（如果需要）
if (!window.Gamepad) {
    window.Gamepad = class Gamepad {
        constructor() {}
    };
}

if (!window.GamepadEvent) {
    window.GamepadEvent = class GamepadEvent {
        constructor() {}
    };
}

// 添加Battery API（如果需要）
if (!navigator.getBattery) {
    navigator.getBattery = () => Promise.resolve({
        charging: true,
        chargingTime: 0,
        dischargingTime: Infinity,
        level: 1
    });
}

// 添加Vibration API（如果需要）
if (!navigator.vibrate) {
    navigator.vibrate = () => true;
}

// 添加Clipboard API（如果需要）
if (!navigator.clipboard) {
    navigator.clipboard = {
        readText: () => Promise.resolve(''),
        writeText: () => Promise.resolve(),
        read: () => Promise.resolve([]),
        write: () => Promise.resolve()
    };
}

// 添加Credentials API（如果需要）
if (!navigator.credentials) {
    navigator.credentials = {
        get: () => Promise.resolve(null),
        create: () => Promise.resolve(null),
        store: () => Promise.resolve(null),
        preventSilentAccess: () => Promise.resolve()
    };
}

// 添加USB API（如果需要）
if (!navigator.usb) {
    navigator.usb = {
        getDevices: () => Promise.resolve([]),
        requestDevice: () => Promise.reject(new Error('Not implemented'))
    };
}

// 添加Bluetooth API（如果需要）
if (!navigator.bluetooth) {
    navigator.bluetooth = {
        getDevices: () => Promise.resolve([]),
        requestDevice: () => Promise.reject(new Error('Not implemented'))
    };
}

// 添加WebUSB API（如果需要）
if (!navigator.serial) {
    navigator.serial = {
        getPorts: () => Promise.resolve([]),
        requestPort: () => Promise.reject(new Error('Not implemented'))
    };
}

// 添加WebHID API（如果需要）
if (!navigator.hid) {
    navigator.hid = {
        getDevices: () => Promise.resolve([]),
        requestDevice: () => Promise.reject(new Error('Not implemented'))
    };
}

// 添加WebXR API（如果需要）
if (!window.XR) {
    window.XR = {
        isSessionSupported: () => Promise.resolve(false)
    };
}

if (!window.XRSession) {
    window.XRSession = class XRSession {
        constructor() {}
    };
}


// 添加必要的DOM API
if (!document.getScripts) {
    document.getScripts = function() {
        return document.scripts || [];
    };
}

// 添加PointerEvent（如果需要）
if (!window.PointerEvent) {
    window.PointerEvent = class PointerEvent extends window.MouseEvent {
        constructor(type, init) {
            super(type, init);
            this.pointerId = init?.pointerId || 0;
            this.width = init?.width || 1;
            this.height = init?.height || 1;
            this.pressure = init?.pressure || 0;
            this.tiltX = init?.tiltX || 0;
            this.tiltY = init?.tiltY || 0;
            this.pointerType = init?.pointerType || '';
            this.isPrimary = init?.isPrimary || false;
        }
    };
}

// 添加TouchEvent（如果需要）
if (!window.TouchEvent) {
    window.TouchEvent = class TouchEvent {
        constructor(type, init) {
            this.type = type;
            this.touches = init?.touches || [];
            this.targetTouches = init?.targetTouches || [];
            this.changedTouches = init?.changedTouches || [];
        }
    };
}

// 添加Touch（如果需要）
if (!window.Touch) {
    window.Touch = class Touch {
        constructor(init) {
            this.identifier = init?.identifier || 0;
            this.target = init?.target || null;
            this.clientX = init?.clientX || 0;
            this.clientY = init?.clientY || 0;
            this.pageX = init?.pageX || 0;
            this.pageY = init?.pageY || 0;
            this.screenX = init?.screenX || 0;
            this.screenY = init?.screenY || 0;
            this.radiusX = init?.radiusX || 0;
            this.radiusY = init?.radiusY || 0;
            this.rotationAngle = init?.rotationAngle || 0;
            this.force = init?.force || 0;
        }
    };
}

// 添加错误处理补丁
// 确保 console.err 存在
if (console && !console.err) {
    console.err = console.error;
}

// 为 Object.prototype 添加 err 方法（谨慎使用）
if (!Object.prototype.err) {
    Object.defineProperty(Object.prototype, 'err', {
        value: function() {
            console.error('err method called on object:', this);
            return this;
        },
        enumerable: false,
        configurable: true,
        writable: true
    });
}

// 创建一个安全的错误处理方法
function addErrorHandler(obj) {
    if (obj && typeof obj === 'object') {
        try {
            Object.defineProperty(obj, 'err', {
                value: function() {
                    console.error('Error handler called:', this, arguments);
                    return this;
                },
                enumerable: false,
                configurable: true,
                writable: true
            });
        } catch (e) {
            console.warn('Could not add err method to object:', e.message);
        }
    }
    return obj;
}

// 使用这个方法来包装可能需要的对象
if (window.XMLHttpRequest) {
    const originalXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = function() {
        return addErrorHandler(new originalXHR());
    };
}

// 对于 Promise，我们可以使用更安全的方式
if (window.Promise && !Promise.prototype.err) {
    const originalThen = Promise.prototype.then;
    Promise.prototype.err = function(callback) {
        return originalThen.call(this, undefined, function(reason) {
            console.error('Promise error:', reason);
            if (typeof callback === 'function') {
                callback(reason);
            }
            return reason;
        });
    };
}

// 创建一个错误处理包装器
function createErrorHandler(obj) {
    if (obj && typeof obj === 'object' && !obj.err) {
        obj.err = function() {
            console.error('Error handler called:', this, arguments);
            return this;
        };
    }
    return obj;
}

// 重写一些可能用到 err 方法的 API
if (window.XMLHttpRequest) {
    const originalXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = function() {
        const xhr = new originalXHR();
        return createErrorHandler(xhr);
    };
}

// 确保 Promise 也有错误处理
if (window.Promise) {
    const originalPromise = window.Promise;
    window.Promise = function(executor) {
        return new originalPromise(executor);
    };

    // 为 Promise 原型添加 err 方法
    if (!Promise.prototype.err) {
        Promise.prototype.err = function(callback) {
            return this.catch(function(reason) {
                console.error('Promise error:', reason);
                if (typeof callback === 'function') {
                    callback(reason);
                }
                return reason;
            });
        };
    }
}


console.log('环境补丁已应用');

require('./vendors.js');

const kwaiApp = require('./kwai_app.js');


t = {
    "url": "/rest/k/live/websocket/info",
    "query": {
        "caver": "2",
        "liveStreamId": "lmTD0vyxXFk"
    },
    "form": {},
    "requestBody": {}
}

window.__WEBPACK_DEFAULT_EXPORT__.call("$encode", [t, {
    suc: function(t, r) {
        e({
            signResult: t,
            signInput: r
        })
    },
    err: function(e) {
        r(e)
    }
}])


// window.__WEBPACK_DEFAULT_EXPORT__.call("$encode", [e, {
//     suc(e, t) {
//         // 处理成功回调
//         console.log('Success:', e);
//         sign4 = e
//     },
//     err(e) {
//         // 处理错误回调
//         console.error('Error:', e);
//         return e
//     }
// }]);

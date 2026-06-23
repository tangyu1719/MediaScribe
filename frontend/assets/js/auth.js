/**
 * 认证与权限管理模块 - 统一处理登录状态、Token管理、RBAC权限控制
 */
(function() {
  'use strict';

  // ==================== 配置 ====================
  const AUTH_CONFIG = {
    TOKEN_KEY: 'sba_token',
    USER_KEY: 'sba_user',
    PUBLIC_PAGES: ['/login.html', '/register.html', '/forgot-password.html'],
    PUBLIC_API_PREFIXES: ['/api/auth/login', '/api/auth/register', '/api/auth/send-code', '/api/health', '/output/'],
    ADMIN_PAGES: ['iag', 'settings'],
    REQUIRES_AUTH_PAGES: ['video', 'subscribe', 'sched', 'orch', 'chat', 'reader', 'tasks', 'agpz', 'rag', 'rss', 'multimodal', 'cache', 'ops', 'webreplay', 'profile', 'settings']
  };

  function _spaPageFromPath(pathname) {
    const p = String(pathname || '/').replace(/^\//, '').split('/')[0];
    if (!p || p === 'index.html') return 'video';
    if (p.endsWith('.html')) return p.replace(/\.html$/, '');
    return p;
  }

  function redirectToLogin(nextPath) {
    const next = nextPath || (window.location.pathname + window.location.search);
    const safe = next && next.startsWith('/') && !next.startsWith('//') && next !== '/login.html';
    const q = safe ? '?next=' + encodeURIComponent(next) : '';
    window.location.replace('/login.html' + q);
  }

  // ==================== 状态管理 ====================
  const AuthState = {
    token: null,
    user: null,
    isAuthenticated: false,
    isAdmin: false,
    permissions: [],
    listeners: [],

    // 订阅状态变化
    subscribe(callback) {
      this.listeners.push(callback);
      return () => {
        const idx = this.listeners.indexOf(callback);
        if (idx > -1) this.listeners.splice(idx, 1);
      };
    },

    // 通知所有订阅者
    notify() {
      const state = {
        token: this.token,
        user: this.user,
        isAuthenticated: this.isAuthenticated,
        isAdmin: this.isAdmin,
        permissions: this.permissions
      };
      this.listeners.forEach(cb => cb(state));
    },

    // 设置认证状态
    setAuth(token, user) {
      this.token = token;
      this.user = user;
      this.isAuthenticated = !!token && !!user;
      this.isAdmin = user && Array.isArray(user.roles) && user.roles.includes('admin');
      this.permissions = user && Array.isArray(user.permissions) ? user.permissions : [];
      
      // 持久化到 localStorage
      if (token) {
        localStorage.setItem(AUTH_CONFIG.TOKEN_KEY, token);
      } else {
        localStorage.removeItem(AUTH_CONFIG.TOKEN_KEY);
      }
      if (user) {
        localStorage.setItem(AUTH_CONFIG.USER_KEY, JSON.stringify(user));
      } else {
        localStorage.removeItem(AUTH_CONFIG.USER_KEY);
      }
      
      this.notify();
    },

    // 清除认证状态
    clearAuth() {
      this.token = null;
      this.user = null;
      this.isAuthenticated = false;
      this.isAdmin = false;
      this.permissions = [];
      localStorage.removeItem(AUTH_CONFIG.TOKEN_KEY);
      localStorage.removeItem(AUTH_CONFIG.USER_KEY);
      this.notify();
    },

    // 从 localStorage 恢复
    restore() {
      const token = localStorage.getItem(AUTH_CONFIG.TOKEN_KEY);
      const userStr = localStorage.getItem(AUTH_CONFIG.USER_KEY);
      if (!token) return false;
      if (!userStr) {
        // 仅有 token：保留供 fetch 与 /api/auth/me 补全，避免误清登录态
        this.token = token;
        this.user = null;
        this.isAuthenticated = false;
        this.isAdmin = false;
        this.permissions = [];
        this.notify();
        return false;
      }
      try {
        const user = JSON.parse(userStr);
        if (user && user.id) {
          this.setAuth(token, user);
          return true;
        }
        localStorage.removeItem(AUTH_CONFIG.USER_KEY);
      } catch (e) {
        localStorage.removeItem(AUTH_CONFIG.USER_KEY);
      }
      this.token = token;
      this.user = null;
      this.isAuthenticated = false;
      this.isAdmin = false;
      this.permissions = [];
      this.notify();
      return false;
    },

    // 检查是否有权限
    hasPermission(permission) {
      if (this.isAdmin) return true;
      return this.permissions.includes(permission);
    },

    // 检查是否可以访问页面
    canAccessPage(page) {
      // 公开页面不需要登录
      if (page === 'login') return true;
      
      // 需要登录的页面
      if (AUTH_CONFIG.REQUIRES_AUTH_PAGES.includes(page)) {
        if (!this.isAuthenticated) return false;
      }
      
      // 管理员页面
      if (AUTH_CONFIG.ADMIN_PAGES.includes(page)) {
        return this.isAdmin;
      }
      
      return true;
    }
  };

  // ==================== API 调用封装 ====================
  const ApiClient = {
    // 检查是否为公开 API
    isPublicApi(url) {
      const urlStr = typeof url === 'string' ? url : '';
      return AUTH_CONFIG.PUBLIC_API_PREFIXES.some(prefix => 
        urlStr.startsWith(prefix) || urlStr.includes(prefix)
      );
    },

    // 构建请求头
    buildHeaders(customHeaders = {}) {
      const headers = {
        'Content-Type': 'application/json',
        ...customHeaders
      };
      
      // 添加认证头
      if (AuthState.token) {
        headers['Authorization'] = `Bearer ${AuthState.token}`;
      }
      
      return headers;
    },

    // 发送请求
    async request(url, options = {}) {
      const isApi = typeof url === 'string' && url.startsWith('/api/');
      const isPublic = this.isPublicApi(url);
      
      // 检查是否需要登录
      if (isApi && !isPublic && !AuthState.isAuthenticated) {
        throw new Error('未登录，请先登录');
      }

      // 构建请求配置
      const config = {
        ...options,
        headers: this.buildHeaders(options.headers)
      };

      // 发送请求
      const response = await fetch(url, config);
      
      // 处理 401 未授权
      if (response.status === 401) {
        AuthState.clearAuth();
        if (!isPublic && window.location.pathname !== '/login.html') {
          redirectToLogin();
        }
        throw new Error('登录已过期，请重新登录');
      }

      // 处理 403 禁止访问（未登录视同需重新认证）
      if (response.status === 403) {
        if (!AuthState.isAuthenticated) {
          redirectToLogin();
        }
        throw new Error('没有权限执行此操作');
      }

      return response;
    },

    // GET 请求
    async get(url, headers = {}) {
      return this.request(url, { method: 'GET', headers });
    },

    // POST 请求
    async post(url, body, headers = {}) {
      return this.request(url, {
        method: 'POST',
        headers,
        body: body ? JSON.stringify(body) : undefined
      });
    },

    // PUT 请求
    async put(url, body, headers = {}) {
      return this.request(url, {
        method: 'PUT',
        headers,
        body: body ? JSON.stringify(body) : undefined
      });
    },

    // DELETE 请求
    async delete(url, headers = {}) {
      return this.request(url, { method: 'DELETE', headers });
    }
  };

  // ==================== 路由守卫 ====================
  const RouterGuard = {
    // 当前页面
    currentPage: 'video',
    
    // 页面切换前的钩子
    beforeEach(to, from) {
      // 检查是否需要登录
      if (AUTH_CONFIG.REQUIRES_AUTH_PAGES.includes(to)) {
        if (!AuthState.isAuthenticated) {
          return { allowed: false, redirect: 'login', message: '请先登录' };
        }
      }

      // 检查管理员权限
      if (AUTH_CONFIG.ADMIN_PAGES.includes(to)) {
        if (!AuthState.isAdmin) {
          return { allowed: false, redirect: 'chat', message: '需要管理员权限' };
        }
      }

      return { allowed: true };
    },

    // 执行导航
    navigate(to) {
      const result = this.beforeEach(to, this.currentPage);
      
      if (!result.allowed) {
        if (result.message) {
          window.showToastMsg && window.showToastMsg(result.message);
        }
        return result.redirect || 'login';
      }

      this.currentPage = to;
      return to;
    },

    // 初始化检查
    init() {
      // 恢复登录状态
      AuthState.restore();
      
      const currentPath = window.location.pathname;
      const isLoginPage = currentPath === '/login.html' || currentPath.endsWith('/login.html');
      
      // 如果在登录页但已登录，跳转到首页
      if (isLoginPage && AuthState.isAuthenticated) {
        window.location.href = '/';
        return false;
      }
      
      if (isLoginPage || AUTH_CONFIG.PUBLIC_PAGES.includes(currentPath)) {
        return true;
      }

      const spaPage = _spaPageFromPath(currentPath);
      const needsAuth = AUTH_CONFIG.REQUIRES_AUTH_PAGES.includes(spaPage) || currentPath === '/' || currentPath === '/index.html';
      
      // SPA 路由：无 token 直接跳转登录（认证优先于页面渲染）
      if (needsAuth && !AuthState.token) {
        redirectToLogin(currentPath + window.location.search);
        return false;
      }
      
      return true;
    }
  };

  // ==================== 初始化 ====================
  // 恢复认证状态
  AuthState.restore();

  // 全局暴露
  window.AuthManager = {
    state: AuthState,
    api: ApiClient,
    router: RouterGuard,
    config: AUTH_CONFIG,
    
    // 快捷方法
    login(token, user) {
      AuthState.setAuth(token, user);
    },
    
    logout() {
      AuthState.clearAuth();
      redirectToLogin();
    },
    
    redirectToLogin(nextPath) {
      redirectToLogin(nextPath);
    },
    
    checkAuth() {
      return AuthState.isAuthenticated;
    },
    
    checkAdmin() {
      return AuthState.isAdmin;
    },
    
    canAccess(page) {
      return AuthState.canAccessPage(page);
    }
  };

  // 拦截原生 fetch，强制添加 token
  const _origFetch = window.fetch;
  window.fetch = function(url, options) {
    if (!options) options = {};
    if (typeof url !== 'string') return _origFetch(url, options);
    var isApi = url.indexOf('/api/') === 0;
    if (!isApi) return _origFetch(url, options);
    var isPublic = false;
    for (var i = 0; i < AUTH_CONFIG.PUBLIC_API_PREFIXES.length; i++) {
      if (url.indexOf(AUTH_CONFIG.PUBLIC_API_PREFIXES[i]) === 0) { isPublic = true; break; }
    }
    if (!isPublic && AuthState.token) {
      if (!options.headers) options.headers = {};
      var h = {};
      if (options.headers instanceof Headers) {
        options.headers.forEach(function(v, k) { h[k] = v; });
      } else {
        for (var k in options.headers) { if (options.headers.hasOwnProperty(k)) h[k] = options.headers[k]; }
      }
      h['Authorization'] = 'Bearer ' + AuthState.token;
      options.headers = h;
    }
    return _origFetch(url, options).then(function(response) {
      if (response.status === 401 && !isPublic) {
        AuthState.clearAuth();
        if (window.location.pathname !== '/login.html') {
          redirectToLogin();
        }
      }
      if (response.status === 403 && !isPublic && !AuthState.isAuthenticated) {
        redirectToLogin();
      }
      return response;
    });
  };
  var _OrigEventSource = window.EventSource;
  window.EventSource = function(url, config) {
    var token = AuthState.token || localStorage.getItem(AUTH_CONFIG.TOKEN_KEY);
    if (token && typeof url === 'string' && url.indexOf('/api/') >= 0) {
      var sep = url.indexOf('?') >= 0 ? '&' : '?';
      url = url + sep + 'sba_token=' + encodeURIComponent(token);
    }
    return new _OrigEventSource(url, config);
  };
  window.EventSource.prototype = _OrigEventSource.prototype;

  // 全局 Promise 错误捕获：未处理的 API 错误显示在页面上
  window.addEventListener('unhandledrejection', function(event) {
    var reason = event.reason;
    var msg = reason ? (reason.message || String(reason)) : '未知错误';
    console.error('[Auth] 未捕获的异步错误: ' + msg);
    var el = document.getElementById('auth-required-mask');
    if (el && reason && reason.message && reason.message.indexOf('未登录') >= 0) {
      el.style.display = 'flex';
    }
  });

})();

// 🌊 Distributed Nostr Network - Optimized 4-Node Architecture
// Гармоничная архитектура: 1 master + 2 storage + 1 gateway

const express = require('express');
const { WebSocketServer } = require('ws');
const crypto = require('crypto');
const WebSocket = require('ws');

// ========================= GOLDEN RATIO CONSTANTS =========================

const GOLDEN_RATIO = 1.618;
const FIBONACCI = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55];

const NETWORK_CONFIG = {
  topology: 'tetrahedral',
  nodeId: process.env.NODE_ID || `node-${Date.now().toString(36)}-${Math.random().toString(36).substr(2, 5)}`,

  // 🎯 Упрощенная 4-узловая архитектура
  roles: {
    master: 1,    // Координация и консенсус
    storage: 2,   // Хранение данных с репликацией
    gateway: 1    // Точка входа для клиентов
  },

  // 🔗 4 основных узла
  peers: [
    process.env.PEER_1, // master-001
    process.env.PEER_2, // storage-001  
    process.env.PEER_3, // storage-002
    process.env.PEER_4  // gateway-001
  ].filter(Boolean),

  // ⏰ Увеличенные timeout'ы для Render
  keepAlive: {
    interval: 30 * 1000,        // 30 секунд (было 12 минут)
    pingUrl: process.env.PING_URL,
    healthCheck: '/heartbeat',
    peerTimeout: 120 * 1000,    // 2 минуты ожидания peer'а
    reconnectDelay: 5 * 1000    // 5 секунд между переподключениями
  },

  consensus: {
    quorum: 3,               // Минимум 3 узла (было 4)
    syncInterval: 15000,     // 15 секунд (было 8)
    conflictResolve: 'timestamp',
    initializationDelay: 10000 // 10 секунд на инициализацию
  },

  replication: {
    factor: 2,              // Репликация на 2 storage узла
    sharding: 'hash',
    backupInterval: 5 * 60 * 1000  // 5 минут
  },

  // 🚀 Render-specific optimizations
  render: {
    coldStartDelay: 30000,   // 30 секунд на cold start
    maxRetries: 5,
    keepWarm: true
  }
};

// ========================= DISTRIBUTED STORAGE ENGINE =========================

class DistributedStorage {
  constructor(nodeId) {
    this.nodeId = nodeId;
    this.role = this.determineRole();
    this.localData = new Map();
    this.replicatedData = new Map();
    this.metadata = new Map();
    this.peers = new Map();

    this.state = {
      isLeader: false,
      lastSync: Date.now(),
      version: 0,
      health: 'initializing',
      isInitialized: false
    };

    this.conflictLog = [];
    this.lastHeartbeat = new Map();
    this.connectionAttempts = new Map();
    this.initializationStartTime = Date.now();

    // 🔄 Delayed initialization for better cold start handling
    setTimeout(() => {
      this.initializeNode();
    }, NETWORK_CONFIG.render.coldStartDelay);
  }

  determineRole() {
    const envRole = process.env.NODE_ROLE;
    if (envRole && ['master', 'storage', 'gateway'].includes(envRole)) {
      return envRole;
    }

    // 🎯 Simplified role assignment based on nodeId
    if (this.nodeId.includes('master')) return 'master';
    if (this.nodeId.includes('storage')) return 'storage';
    if (this.nodeId.includes('gateway')) return 'gateway';

    // Hash-based fallback
    const hash = crypto.createHash('sha256').update(this.nodeId).digest('hex');
    const hashValue = parseInt(hash.slice(0, 8), 16);
    const position = (hashValue % 1000) / 1000;

    if (position < 0.25) return 'master';
    if (position < 0.75) return 'storage'; 
    return 'gateway';
  }

  async initializeNode() {
    console.log(`🌊 Node ${this.nodeId} starting as ${this.role}`);
    
    this.state.health = 'connecting';
    
    // 🔄 Sequential connection with delays for better stability
    await this.connectToPeersSequentially();
    
    // ⏰ Wait for peer introductions
    await this.waitForPeerIntroductions();
    
    this.startConsensusLoop();
    this.startKeepAlive();
    this.initializeRoleServices();
    
    this.state.isInitialized = true;
    this.state.health = 'healthy';
    
    console.log(`✅ Node ${this.nodeId} fully initialized`);
  }

  async connectToPeersSequentially() {
    for (const [index, peerUrl] of NETWORK_CONFIG.peers.entries()) {
      if (!peerUrl || peerUrl.includes(this.nodeId)) continue;

      // 🌊 Staggered connection delays for graceful startup
      const delay = index * 2000; // 2 секунды между подключениями
      
      setTimeout(async () => {
        await this.connectToPeer(peerUrl);
      }, delay);
    }
  }

  async connectToPeer(peerUrl) {
    if (this.connectionAttempts.get(peerUrl) >= NETWORK_CONFIG.render.maxRetries) {
      console.log(`❌ Max retries reached for ${peerUrl}`);
      return;
    }

    try {
      let wsUrl = peerUrl;
      if (peerUrl.startsWith('https://')) {
        wsUrl = peerUrl.replace('https://', 'wss://');
      } else if (peerUrl.startsWith('http://')) {
        wsUrl = peerUrl.replace('http://', 'ws://');
      }

      if (!wsUrl.includes('/ws') && !wsUrl.endsWith('/')) {
        wsUrl = wsUrl + '/ws';
      }

      console.log(`🔗 Connecting to: ${wsUrl}`);

      const ws = new WebSocket(wsUrl);
      const connectTimeout = setTimeout(() => {
        console.log(`⏰ Connection timeout for ${peerUrl}`);
        ws.terminate();
      }, NETWORK_CONFIG.keepAlive.peerTimeout);

      ws.on('open', () => {
        clearTimeout(connectTimeout);
        console.log(`🤝 Connected to peer: ${peerUrl}`);
        
        this.peers.set(peerUrl, {
          ws,
          lastSeen: Date.now(),
          health: 'connected',
          nodeId: null,
          role: null,
          introducedAt: null
        });

        // 🔄 Send introduction immediately
        this.sendToPeer(ws, {
          type: 'node_intro',
          nodeId: this.nodeId,
          role: this.role,
          timestamp: Date.now(),
          version: this.state.version
        });

        // Reset connection attempts on success
        this.connectionAttempts.set(peerUrl, 0);
      });

      ws.on('message', (data) => this.handlePeerMessage(peerUrl, data));
      ws.on('close', () => this.handlePeerDisconnect(peerUrl));
      ws.on('error', (error) => {
        clearTimeout(connectTimeout);
        console.log(`❌ WebSocket error for ${peerUrl}:`, error.message);
        
        const attempts = this.connectionAttempts.get(peerUrl) || 0;
        this.connectionAttempts.set(peerUrl, attempts + 1);
        
        setTimeout(() => {
          this.reconnectToPeer(peerUrl);
        }, NETWORK_CONFIG.keepAlive.reconnectDelay * (attempts + 1));
      });

    } catch (error) {
      console.log(`❌ Failed to connect to ${peerUrl}:`, error.message);
      
      const attempts = this.connectionAttempts.get(peerUrl) || 0;
      this.connectionAttempts.set(peerUrl, attempts + 1);
    }
  }

  async waitForPeerIntroductions() {
    const maxWait = 30000; // 30 секунд ожидания
    const checkInterval = 1000; // Проверяем каждую секунду
    let waited = 0;

    while (waited < maxWait) {
      const introducedPeers = Array.from(this.peers.values())
        .filter(peer => peer.nodeId && peer.role && peer.introducedAt);
      
      console.log(`🔍 Waiting for introductions: ${introducedPeers.length}/${this.peers.size} peers ready`);
      
      if (introducedPeers.length >= Math.min(2, this.peers.size)) {
        console.log(`✅ Sufficient peer introductions received`);
        break;
      }
      
      await new Promise(resolve => setTimeout(resolve, checkInterval));
      waited += checkInterval;
    }
  }

  handlePeerMessage(peerUrl, data) {
    try {
      const message = JSON.parse(data.toString());

      switch (message.type) {
        case 'node_intro':
          this.handleNodeIntro(peerUrl, message);
          break;
        case 'query_request':
          this.handleQueryRequest(peerUrl, message);
          break;
        case 'consensus_proposal':
          this.handleConsensusProposal(message);
          break;
        case 'nostr_event':
          this.handleNostrEvent(message);
          break;
        case 'data_sync':
          this.handleDataSync(message);
          break;
        case 'heartbeat':
          this.handleHeartbeat(peerUrl, message);
          break;
        case 'sync_request':
          this.handleSyncRequest(peerUrl, message);
          break;
        case 'sync_response':
          this.handleSyncResponse(message);
          break;
        case 'post_expired':
          this.handlePostExpired(message);
          break;
      }
    } catch (error) {
      console.log('❌ Error handling peer message:', error);
    }
  }

  handleNodeIntro(peerUrl, message) {
    const peer = this.peers.get(peerUrl);
    if (peer) {
      peer.nodeId = message.nodeId;
      peer.role = message.role;
      peer.lastSeen = Date.now();
      peer.introducedAt = Date.now();
      
      console.log(`👋 Peer introduced: ${message.nodeId} (${message.role})`);

      // 🔄 Send our introduction back if not already sent
      this.sendToPeer(peer.ws, {
        type: 'node_intro_ack',
        nodeId: this.nodeId,
        role: this.role,
        timestamp: Date.now(),
        version: this.state.version
      });

      // 📦 Request initial data sync
      this.requestDataSync(peer.ws);
    }
  }

  getActivePeers() {
    const now = Date.now();
    const timeout = NETWORK_CONFIG.keepAlive.peerTimeout;

    return Array.from(this.peers.entries())
      .filter(([url, peer]) => {
        const isRecentlySeen = now - peer.lastSeen < timeout;
        const hasNodeId = peer.nodeId && peer.nodeId !== this.nodeId;
        const isIntroduced = peer.introducedAt !== null;
        
        return isRecentlySeen && hasNodeId && isIntroduced;
      })
      .map(([url, peer]) => ({ 
        url, 
        nodeId: peer.nodeId, 
        role: peer.role, 
        ...peer 
      }));
  }

  startConsensusLoop() {
    // 🔄 More conservative consensus timing
    setInterval(() => {
      if (this.state.isInitialized) {
        this.performConsensusRound();
      }
    }, NETWORK_CONFIG.consensus.syncInterval);

    setInterval(() => {
      if (this.state.isInitialized) {
        this.electLeader();
      }
    }, FIBONACCI[7] * 1000);
  }

  async performConsensusRound() {
    const activePeers = this.getActivePeers();

    if (activePeers.length < NETWORK_CONFIG.consensus.quorum - 1) {
      if (this.state.isInitialized) {
        console.log(`⚠️ Insufficient peers for consensus: ${activePeers.length}/${NETWORK_CONFIG.consensus.quorum - 1} required`);
      }
      return;
    }

    const proposal = {
      type: 'consensus_proposal',
      nodeId: this.nodeId,
      version: this.state.version + 1,
      changes: this.getPendingChanges(),
      timestamp: Date.now()
    };

    this.broadcastToPeers(proposal);
  }

  electLeader() {
    const activePeers = this.getActivePeers();
    const candidates = [this.nodeId, ...activePeers.map(p => p.nodeId)]
      .filter(Boolean)
      .sort();
    
    const leader = candidates[0];
    const wasLeader = this.state.isLeader;
    this.state.isLeader = (leader === this.nodeId);

    if (this.state.isLeader && !wasLeader) {
      console.log(`👑 Node ${this.nodeId} became leader`);
      this.onBecomeLeader();
    }
  }

  onBecomeLeader() {
    this.startDataSynchronization();
    this.startConflictResolution();
    this.coordinateReplication();
  }

  startKeepAlive() {
    setInterval(() => {
      this.pingAllPeers();
      this.checkNetworkHealth();
    }, NETWORK_CONFIG.keepAlive.interval);

    // 🔄 Self-ping to prevent Render cold start
    if (NETWORK_CONFIG.render.keepWarm && NETWORK_CONFIG.keepAlive.pingUrl) {
      setInterval(async () => {
        try {
          const response = await fetch(NETWORK_CONFIG.keepAlive.pingUrl + '/heartbeat');
          console.log(`💓 Self-ping: ${response.status}`);
        } catch (error) {
          console.log('⚠️ Self-ping failed:', error.message);
        }
      }, 10 * 60 * 1000); // Каждые 10 минут
    }
  }

  checkNetworkHealth() {
    const activePeers = this.getActivePeers();
    const peerCount = activePeers.length;
    const totalPeers = NETWORK_CONFIG.peers.length - 1; // -1 для исключения себя

    console.log(`💓 Network health: ${peerCount}/${totalPeers} nodes active`);

    if (peerCount < NETWORK_CONFIG.consensus.quorum - 1) {
      if (this.state.health !== 'degraded') {
        console.log('⚠️ Network partition detected - entering degraded mode');
        this.state.health = 'degraded';
      }
    } else {
      this.state.health = 'healthy';
    }
  }

  pingAllPeers() {
    const heartbeat = {
      type: 'heartbeat',
      nodeId: this.nodeId,
      timestamp: Date.now(),
      health: this.state.health,
      role: this.role
    };
    this.broadcastToPeers(heartbeat);
  }

  sendToPeer(ws, message) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(message));
    }
  }

  broadcastToPeers(message) {
    for (const peer of this.peers.values()) {
      this.sendToPeer(peer.ws, message);
    }
  }

  handleHeartbeat(peerUrl, message) {
    const peer = this.peers.get(peerUrl);
    if (peer) {
      peer.lastSeen = Date.now();
      peer.health = message.health || 'unknown';
    }
  }

  async reconnectToPeer(peerUrl) {
    console.log(`🔄 Attempting to reconnect to ${peerUrl}`);
    
    const peer = this.peers.get(peerUrl);
    if (peer && peer.ws.readyState === WebSocket.CLOSED) {
      this.peers.delete(peerUrl);
      await this.connectToPeer(peerUrl);
    }
  }

  handlePeerDisconnect(peerUrl) {
    console.log(`💔 Peer disconnected: ${peerUrl}`);
    const peer = this.peers.get(peerUrl);
    if (peer) {
      peer.health = 'disconnected';
      peer.lastSeen = Date.now();
    }

    // 🔄 Schedule reconnection
    setTimeout(() => {
      this.reconnectToPeer(peerUrl);
    }, NETWORK_CONFIG.keepAlive.reconnectDelay);
  }

  // ============= ROLE-SPECIFIC SERVICES =============

  initializeRoleServices() {
    switch (this.role) {
      case 'master':
        this.startMasterServices();
        break;
      case 'storage':
        this.startStorageServices();
        break;
      case 'gateway':
        this.startGatewayServices();
        break;
    }
  }

  startMasterServices() {
    console.log('👑 Starting master node services');
    
    // 🎯 Master coordinates the network
    setInterval(() => {
      this.checkNetworkHealth();
      this.coordinateDataDistribution();
    }, FIBONACCI[7] * 1000);
  }

  startStorageServices() {
    console.log('📦 Starting storage node services');
    
    // 🗄️ Storage handles data persistence and replication
    this.initializeDataSharding();
    this.startReplicationService();
  }

  startGatewayServices() {
    console.log('🌐 Starting gateway node services');
    
    // 🔀 Gateway handles client connections and load balancing
    this.setupClientRouting();
    this.startLoadBalancing();
  }

  // ============= PLACEHOLDER METHODS (implement as needed) =============
  
  cleanupOldPosts() { /* Implementation */ }
  selectStorageNodes(eventId) { return { primary: 'storage-001', replica: 'storage-002' }; }
  addNostrEvent(event) { /* Implementation */ }
  queryNostrEvents(filters) { /* Implementation */ }
  convertNostrEventToPost(event) { /* Implementation */ }
  convertPostToNostrEvent(post) { /* Implementation */ }
  eventMatchesFilters(event, filters) { /* Implementation */ }
  handleQueryRequest(peerUrl, message) { /* Implementation */ }
  handleConsensusProposal(message) { /* Implementation */ }
  handleNostrEvent(message) { /* Implementation */ }
  handleDataSync(message) { /* Implementation */ }
  handleSyncRequest(peerUrl, message) { /* Implementation */ }
  handleSyncResponse(message) { /* Implementation */ }
  handlePostExpired(message) { /* Implementation */ }
  requestDataSync(ws) { /* Implementation */ }
  getPendingChanges() { return []; }
  startDataSynchronization() { /* Implementation */ }
  startConflictResolution() { /* Implementation */ }
  coordinateReplication() { /* Implementation */ }
  coordinateDataDistribution() { /* Implementation */ }
  initializeDataSharding() { /* Implementation */ }
  startReplicationService() { /* Implementation */ }
  setupClientRouting() { /* Implementation */ }
  startLoadBalancing() { /* Implementation */ }
}

// ========================= EXPRESS API LAYER =========================

class DistributedAPI {
  constructor(storage) {
    this.storage = storage;
    this.app = express();
    this.wss = null;
    this.setupMiddleware();
    this.setupRoutes();
  }

  setupMiddleware() {
    this.app.use(express.json({ limit: '1mb' }));
    this.app.use((req, res, next) => {
      res.header('Access-Control-Allow-Origin', '*');
      res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      res.header('Access-Control-Allow-Headers', 'Content-Type');
      next();
    });
  }

  setupRoutes() {
    // Health check with detailed network status
    this.app.get('/heartbeat', (req, res) => {
      const activePeers = this.storage.getActivePeers();
      
      res.json({
        status: 'alive',
        nodeId: this.storage.nodeId,
        role: this.storage.role,
        timestamp: Date.now(),
        network: {
          activePeers: activePeers.length,
          totalPeers: NETWORK_CONFIG.peers.length - 1,
          health: this.storage.state.health,
          isLeader: this.storage.state.isLeader,
          initialized: this.storage.state.isInitialized
        },
        peers: activePeers.map(p => ({
          nodeId: p.nodeId,
          role: p.role,
          health: p.health
        }))
      });
    });

    // Main route
    this.app.get('/', (req, res) => {
      const activePeers = this.storage.getActivePeers();
      
      res.json({
        service: 'Distributed Nostr Relay',
        node: this.storage.nodeId,
        role: this.storage.role,
        network: `${activePeers.length}/${NETWORK_CONFIG.peers.length - 1} nodes`,
        status: this.storage.state.health,
        initialized: this.storage.state.isInitialized,
        websocket: `wss://${req.headers.host}/ws`
      });
    });

    // Network status
    this.app.get('/api/network', (req, res) => {
      const activePeers = this.storage.getActivePeers();
      
      res.json({
        nodeId: this.storage.nodeId,
        role: this.storage.role,
        isLeader: this.storage.state.isLeader,
        initialized: this.storage.state.isInitialized,
        health: this.storage.state.health,
        peers: activePeers,
        network: {
          topology: NETWORK_CONFIG.topology,
          consensus: {
            quorum: NETWORK_CONFIG.consensus.quorum,
            version: this.storage.state.version
          },
          uptime: Date.now() - this.storage.initializationStartTime
        }
      });
    });
  }

  setupWebSocketServer(server) {
    this.wss = new WebSocketServer({
      server,
      path: '/ws'
    });

    this.wss.on('connection', (ws, req) => {
      const clientIp = req.socket.remoteAddress;
      console.log(`🔌 Client connected from ${clientIp} to ${this.storage.role} node`);

      ws.send(JSON.stringify(['NOTICE', `Connected to distributed Nostr relay (${this.storage.role} node)`]));

      ws.on('message', async (data) => {
        try {
          const message = JSON.parse(data.toString());
          
          if (!Array.isArray(message)) {
            ws.send(JSON.stringify(['NOTICE', 'Invalid message format - expected array']));
            return;
          }

          const [type, ...params] = message;

          switch (type) {
            case 'EVENT':
              await this.handleNostrEvent(ws, params[0]);
              break;
            case 'REQ':
              await this.handleNostrRequest(ws, params[0], params.slice(1));
              break;
            case 'CLOSE':
              this.handleNostrClose(ws, params[0]);
              break;
            default:
              ws.send(JSON.stringify(['NOTICE', `Unknown message type: ${type}`]));
          }
        } catch (error) {
          console.error('❌ Error processing Nostr message:', error);
          ws.send(JSON.stringify(['NOTICE', 'Error processing message']));
        }
      });

      ws.on('close', () => {
        console.log(`💔 Client disconnected from ${this.storage.role} node`);
      });

      ws.on('error', (error) => {
        console.error('❌ WebSocket error:', error);
      });
    });
  }

  // Placeholder Nostr handlers
  async handleNostrEvent(ws, event) {
    ws.send(JSON.stringify(['OK', event?.id || '', true, '']));
  }

  async handleNostrRequest(ws, subscriptionId, filters) {
    ws.send(JSON.stringify(['EOSE', subscriptionId]));
  }

  handleNostrClose(ws, subscriptionId) {
    console.log(`🔒 Subscription ${subscriptionId} closed`);
  }

  start(port = 8080) {
    const server = this.app.listen(port, '0.0.0.0', () => {
      console.log(`🌊 Distributed node ${this.storage.nodeId} (${this.storage.role}) running on port ${port}`);
    });

    this.setupWebSocketServer(server);
    return server;
  }
}

// ========================= INITIALIZATION =========================

async function initializeDistributedNode() {
  console.log('🌊 Initializing distributed Nostr node...');

  const storage = new DistributedStorage(NETWORK_CONFIG.nodeId);
  const api = new DistributedAPI(storage);
  const server = api.start();

  process.on('SIGTERM', () => {
    console.log('🛑 Graceful shutdown initiated');
    server.close(() => {
      console.log('✅ Node shutdown complete');
      process.exit(0);
    });
  });

  console.log('🚀 Distributed node initialization complete');
  console.log(`📊 Network topology: ${NETWORK_CONFIG.topology}`);
  console.log(`🎯 Node role: ${storage.role}`);
}

if (require.main === module) {
  initializeDistributedNode().catch(console.error);
}

module.exports = { DistributedStorage, DistributedAPI, NETWORK_CONFIG };

#!/usr/bin/env node
/**
 * Distributed Events Server - Peer-to-Peer Architecture
 * =====================================================
 * 
 * Каждый сервер равноправен и может:
 * - Обрабатывать запросы от фронтенда
 * - Синхронизироваться с другими серверами
 * - Заменить любой другой сервер при сбое
 * - Хранить полный кеш событий (500MB)
 */

const express = require('express')
const http = require('http')
const WebSocket = require('ws')
const TelegramBot = require('node-telegram-bot-api')
const cors = require('cors')
const { Pool } = require('pg')
const Redis = require('ioredis')
const crypto = require('crypto')

class DistributedEventServer {
  constructor() {
    // Уникальная идентификация сервера
    this.serverId = process.env.SERVER_ID || `server_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    this.region = process.env.REGION || 'US'
    this.port = process.env.PORT || 3000

    // Peer-to-peer конфигурация
    this.peers = this.parsePeers(process.env.PEER_SERVERS || '')
    this.isLeader = false
    this.lastLeaderPing = Date.now()
    this.leaderTimeout = 30000 // 30 секунд

    // Локальный кеш (500MB limit)
    this.localCache = new Map() // eventId -> event
    this.cacheMetadata = new Map() // eventId -> {timestamp, size, accessCount}
    this.maxCacheSize = 500 * 1024 * 1024 // 500MB в байтах
    this.currentCacheSize = 0

    this.cacheFile = './events_cache.json'

    // Синхронизация
    this.lastSyncTime = 0
    this.syncInterval = 30000 // 30 секунд
    this.conflictResolution = 'last_write_wins' // или 'vector_clocks'

    this.initializeServices()
  }

  parsePeers(peerString) {
    /**
     * Парсит строку пиров вида: "server1.com:3000,server2.com:3000"
     */
    if (!peerString) return []

    return peerString.split(',').map(peer => {
      const [host, port] = peer.trim().split(':')
      return { host, port: parseInt(port) || 3000, id: `${host}_${port}` }
    }).filter(peer => peer.host && !this.isOwnServer(peer))
  }

  isOwnServer(peer) {
    const ownHost = process.env.RENDER_EXTERNAL_HOSTNAME || 'localhost'
    return peer.host === ownHost && peer.port === this.port
  }

  async initializeServices() {
    // 1. Express приложение
    this.app = express()
    this.server = http.createServer(this.app)
    this.wss = new WebSocket.Server({ server: this.server })

    // 2. База данных (общая для всех серверов)
    if (process.env.DATABASE_URL && process.env.DATABASE_URL !== 'disabled') {
      this.db = new Pool({
        connectionString: process.env.DATABASE_URL,
        ssl: process.env.NODE_ENV === 'production',
        max: 3
      })
    } else {
      this.db = null
      console.log('📝 Database disabled - running in memory-only mode')
    }

    // 3. Redis для pub/sub между серверами (опционально)
    if (process.env.REDIS_URL) {
      this.redis = new Redis(process.env.REDIS_URL)
      this.redisSub = new Redis(process.env.REDIS_URL)
    }

    // 4. Telegram бот
    if (process.env.BOT_TOKEN) {
      this.telegramBot = new TelegramBot(process.env.BOT_TOKEN)
      this.telegramGroupId = process.env.GROUP_ID
    }

    // 5. WebSocket клиенты
    this.wsClients = new Set()

    this.setupMiddleware()
    this.setupRoutes()
    this.setupWebSocket()
    this.setupPeerToPeer()

    if (this.db) {
      await this.initializeDatabase()
      await this.loadCacheFromDatabase()
    }
    await this.startLeaderElection()
    this.startPeriodicTasks()

    this.server.listen(this.port, () => {
      console.log(`🚀 Distributed Server [${this.serverId}] running on port ${this.port}`)
      console.log(`📍 Region: ${this.region}`)
      console.log(`👥 Peers: ${this.peers.map(p => p.id).join(', ') || 'None'}`)
      console.log(`💾 Cache: 0MB / 500MB`)
    })
  }

  setupMiddleware() {
    this.app.use(cors())
    this.app.use(express.json({ limit: '10mb' }))

    // Health check
    this.app.get('/health', (req, res) => {
      res.json({
        serverId: this.serverId,
        region: this.region,
        isLeader: this.isLeader,
        cacheSize: this.formatBytes(this.currentCacheSize),
        peersCount: this.peers.length,
        eventsCount: this.localCache.size,
        uptime: process.uptime(),
        status: 'healthy'
      })
    })
  }

  setupRoutes() {
    // === API для фронтенда ===

    // Получить ленту событий
    this.app.get('/api/feed', async (req, res) => {
      try {
        const { page = 1, limit = 20, search, city, category } = req.query
        const events = await this.getEventsFromCache({ page, limit, search, city, category })

        res.json({
          posts: events,
          hasMore: events.length === parseInt(limit),
          total: this.localCache.size,
          serverId: this.serverId
        })
      } catch (error) {
        console.error('Feed error:', error)
        res.status(500).json({ error: 'Failed to fetch events' })
      }
    })

    // Создать событие
    this.app.post('/api/events', async (req, res) => {
      try {
        const eventData = req.body
        const event = await this.createEvent(eventData)
        res.json(event)
      } catch (error) {
        console.error('Create event error:', error)
        res.status(500).json({ error: 'Failed to create event' })
      }
    })

    // Обновить событие
    this.app.put('/api/events/:id', async (req, res) => {
      try {
        const { id } = req.params
        const updates = req.body
        const event = await this.updateEvent(id, updates)
        res.json(event)
      } catch (error) {
        console.error('Update event error:', error)
        res.status(500).json({ error: 'Failed to update event' })
      }
    })

    // Удалить событие
    this.app.delete('/api/events/:id', async (req, res) => {
      try {
        const { id } = req.params
        await this.deleteEvent(id)
        res.json({ success: true })
      } catch (error) {
        console.error('Delete event error:', error)
        res.status(500).json({ error: 'Failed to delete event' })
      }
    })

    // === API для peer-to-peer синхронизации ===

    // Получить события для синхронизации
    this.app.get('/api/sync/events', this.authenticatePeer.bind(this), async (req, res) => {
      try {
        const { since, limit = 100 } = req.query
        const events = this.getEventsForSync(since, limit)

        res.json({
          events,
          serverId: this.serverId,
          timestamp: Date.now()
        })
      } catch (error) {
        console.error('Sync events error:', error)
        res.status(500).json({ error: 'Sync failed' })
      }
    })

    // Получить синхронизацию от пира
    this.app.post('/api/sync/receive', this.authenticatePeer.bind(this), async (req, res) => {
      try {
        const { events, fromServerId, timestamp } = req.body
        await this.receiveSyncEvents(events, fromServerId, timestamp)

        res.json({
          success: true,
          receivedCount: events.length,
          serverId: this.serverId
        })
      } catch (error) {
        console.error('Receive sync error:', error)
        res.status(500).json({ error: 'Failed to receive sync' })
      }
    })

    // Пинг от другого сервера
    this.app.post('/api/peer/ping', this.authenticatePeer.bind(this), (req, res) => {
      const { fromServerId, isLeader } = req.body

      if (isLeader) {
        this.lastLeaderPing = Date.now()
        if (this.isLeader && fromServerId !== this.serverId) {
          console.log(`⚠️ Conflicting leader detected: ${fromServerId}`)
          this.resolveLeaderConflict(fromServerId)
        }
      }

      res.json({
        serverId: this.serverId,
        isLeader: this.isLeader,
        timestamp: Date.now()
      })
    })
  }

  authenticatePeer(req, res, next) {
    // Простая аутентификация пиров (в продакшене - JWT или подписи)
    const peerToken = req.headers['x-peer-token']
    const expectedToken = process.env.PEER_TOKEN || 'default_peer_token'

    if (peerToken !== expectedToken) {
      return res.status(401).json({ error: 'Unauthorized peer' })
    }

    next()
  }

  setupWebSocket() {
    this.wss.on('connection', (ws) => {
      this.wsClients.add(ws)
      console.log(`📡 Client connected (${this.wsClients.size} total)`)

      ws.on('close', () => {
        this.wsClients.delete(ws)
        console.log(`📡 Client disconnected (${this.wsClients.size} remaining)`)
      })

      ws.on('message', async (data) => {
        try {
          const message = JSON.parse(data.toString())
          await this.handleWebSocketMessage(message, ws)
        } catch (error) {
          console.error('WebSocket message error:', error)
        }
      })
    })
  }

  async handleWebSocketMessage(message, ws) {
    const { type, data } = message

    switch (type) {
      case 'CREATE_EVENT':
        try {
          const event = await this.createEvent(data)
          ws.send(JSON.stringify({ type: 'CREATE_EVENT_SUCCESS', data: event }))
        } catch (error) {
          ws.send(JSON.stringify({ type: 'CREATE_EVENT_ERROR', error: error.message }))
        }
        break

      // ← ДОБАВЬ ЭТИ ОБРАБОТЧИКИ:
      case 'UPDATE_EVENT':
        try {
          const { id, ...updates } = data
          const event = await this.updateEvent(id, updates)
          ws.send(JSON.stringify({ type: 'UPDATE_EVENT_SUCCESS', data: event }))
        } catch (error) {
          ws.send(JSON.stringify({ type: 'UPDATE_EVENT_ERROR', error: error.message }))
        }
        break

      case 'DELETE_EVENT':
        try {
          const { id } = data
          await this.deleteEvent(id)
          ws.send(JSON.stringify({ type: 'DELETE_EVENT_SUCCESS', data: { id } }))
        } catch (error) {
          ws.send(JSON.stringify({ type: 'DELETE_EVENT_ERROR', error: error.message }))
        }
        break

      case 'LIKE_EVENT':
        try {
          const { id, isLiked } = data
          const event = await this.likeEvent(id, isLiked)
          this.broadcastToClients('EVENT_LIKED', { id, isLiked, likes: event.likes })
          ws.send(JSON.stringify({ type: 'LIKE_EVENT_SUCCESS', data: event }))
        } catch (error) {
          ws.send(JSON.stringify({ type: 'LIKE_EVENT_ERROR', error: error.message }))
        }
        break

      default:
        ws.send(JSON.stringify({ type: 'ERROR', error: 'Unknown message type' }))
    }
  }

  broadcastToClients(type, data) {
    const message = JSON.stringify({ type, data })
    this.wsClients.forEach(client => {
      if (client.readyState === WebSocket.OPEN) {
        client.send(message)
      }
    })
  }

  // === PEER-TO-PEER СИСТЕМА ===

  setupPeerToPeer() {
    // Настройка Redis pub/sub для мгновенного уведомления пиров
    if (this.redis) {
      this.redisSub.subscribe('events_channel')
      this.redisSub.on('message', (channel, message) => {
        if (channel === 'events_channel') {
          this.handlePeerNotification(JSON.parse(message))
        }
      })
    }
  }

  async startLeaderElection() {
    // Простой алгоритм выбора лидера - сервер с наименьшим ID
    const allServerIds = [this.serverId, ...this.peers.map(p => p.id)].sort()
    const shouldBeLeader = allServerIds[0] === this.serverId

    if (shouldBeLeader && !this.isLeader) {
      console.log(`👑 ${this.serverId} elected as leader`)
      this.isLeader = true
      await this.announceLeadership()
    }

    // Проверяем лидера каждые 10 секунд
    setInterval(() => {
      this.checkLeaderHealth()
    }, 10000)
  }

  async announceLeadership() {
    // Уведомляем всех пиров о лидерстве
    for (const peer of this.peers) {
      try {
        await this.pingPeer(peer, true)
      } catch (error) {
        console.log(`Failed to announce leadership to ${peer.id}:`, error.message)
      }
    }
  }

  checkLeaderHealth() {
    if (!this.isLeader && Date.now() - this.lastLeaderPing > this.leaderTimeout) {
      console.log(`💀 Leader timeout detected, starting new election`)
      this.startLeaderElection()
    }
  }

  async resolveLeaderConflict(conflictingLeaderId) {
    // Разрешение конфликта лидерства - выбираем сервер с меньшим ID
    if (conflictingLeaderId < this.serverId) {
      console.log(`🤝 Stepping down from leadership in favor of ${conflictingLeaderId}`)
      this.isLeader = false
    }
  }

  startPeriodicTasks() {
    // Синхронизация с пирами
    setInterval(() => {
      this.syncWithPeers()
    }, this.syncInterval)

    // Очистка кеша
    setInterval(() => {
      this.cleanupCache()
    }, 300000) // 5 минут

    // Anti-sleep пинги
    setInterval(() => {
      this.performAntiSleepPings()
    }, 600000) // 10 минут

    // Пинг пиров
    setInterval(() => {
      this.pingAllPeers()
    }, 15000) // 15 секунд
  }

  saveCacheToFile() {
    try {
      const cacheData = {
        events: Array.from(this.localCache.values()),
        timestamp: Date.now()
      }
      require('fs').writeFileSync(this.cacheFile, JSON.stringify(cacheData, null, 2))
      console.log('💾 Cache saved to file')
    } catch (error) {
      console.error('Failed to save cache to file:', error)
    }
  }

  loadCacheFromFile() {
    try {
      if (require('fs').existsSync(this.cacheFile)) {
        const cacheData = JSON.parse(require('fs').readFileSync(this.cacheFile, 'utf8'))
        const events = cacheData.events || []

        for (const event of events) {
          this.addToCache(event.id, event)
        }

        console.log(`💾 Loaded ${events.length} events from file`)
      }
    } catch (error) {
      console.error('Failed to load cache from file:', error)
    }
  }

  // === УПРАВЛЕНИЕ КЕШЕМ ===

  async loadCacheFromDatabase() {
    try {
      if (!this.db) {
        console.log('💾 Database disabled - cache will start empty')
        this.loadCacheFromFile()
        return
      }

      const result = await this.db.query(`
        SELECT * FROM events 
        WHERE status = 'active' 
        ORDER BY created_at DESC 
        LIMIT 1000
      `)

      for (const row of result.rows) {
        const event = this.formatEventFromDB(row)
        this.addToCache(event.id, event)
      }

      console.log(`💾 Loaded ${result.rows.length} events from database`)
    } catch (error) {
      console.error('Failed to load cache from database:', error)
    }
  }

  addToCache(eventId, event) {
    const eventSize = this.calculateEventSize(event)

    // Проверяем лимит кеша
    if (this.currentCacheSize + eventSize > this.maxCacheSize) {
      this.evictLRUEvents(eventSize)
    }

    this.localCache.set(eventId, event)
    this.cacheMetadata.set(eventId, {
      timestamp: Date.now(),
      size: eventSize,
      accessCount: 1
    })
    this.currentCacheSize += eventSize

    console.log(`📥 Added to cache: ${eventId} (${this.formatBytes(eventSize)})`)
  }

  evictLRUEvents(neededSpace) {
    // Удаляем наименее используемые события
    const sorted = Array.from(this.cacheMetadata.entries())
      .sort((a, b) => a[1].accessCount - b[1].accessCount || a[1].timestamp - b[1].timestamp)

    let freedSpace = 0
    for (const [eventId, metadata] of sorted) {
      if (freedSpace >= neededSpace) break

      this.localCache.delete(eventId)
      this.cacheMetadata.delete(eventId)
      this.currentCacheSize -= metadata.size
      freedSpace += metadata.size

      console.log(`🗑️ Evicted from cache: ${eventId}`)
    }
  }

  calculateEventSize(event) {
    // Примерный расчет размера события в байтах
    return JSON.stringify(event).length * 2 // UTF-16 encoding
  }

  formatBytes(bytes) {
    return (bytes / (1024 * 1024)).toFixed(1) + 'MB'
  }

  // === CRUD ОПЕРАЦИИ ===

  async createEvent(eventData) {
    const event = {
      id: `${this.serverId}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      title: eventData.title,
      description: eventData.description,
      authorId: eventData.authorId,
      author: eventData.author,
      city: eventData.city || '',
      category: eventData.category || '',
      likes: 0,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      status: 'active',
      serverId: this.serverId,
      version: 1
    }

    // Сохраняем в базу данных
    //await this.saveEventToDB(event)

    // Добавляем в локальный кеш
    this.addToCache(event.id, event)

    // Уведомляем пиров
    await this.notifyPeers('EVENT_CREATED', event)

    // Отправляем в Telegram (только лидер)
    if (this.isLeader && this.telegramBot) {
      await this.sendToTelegram(event)
    }

    // Уведомляем WebSocket клиентов
    this.broadcastToClients('EVENT_CREATED', event)

    console.log(`✅ Event created: ${event.title} (${event.id})`)
    return event
  }

  async updateEvent(eventId, updates) {
    let event = this.localCache.get(eventId)

    if (!event) {
      if (this.db) {
        event = await this.loadEventFromDB(eventId)
      }
      if (!event) {
        throw new Error('Event not found')
      }
    }

    // Обновляем событие
    const updatedEvent = {
      ...event,
      ...updates,
      updatedAt: new Date().toISOString(),
      version: event.version + 1
    }

    // Сохраняем в базу если есть
    if (this.db) {
      await this.saveEventToDB(updatedEvent)
    }

    // Обновляем кеш
    this.addToCache(eventId, updatedEvent)

    // Уведомляем пиров
    await this.notifyPeers('EVENT_UPDATED', updatedEvent)

    // Уведомляем клиентов
    this.broadcastToClients('EVENT_UPDATED', updatedEvent)

    return updatedEvent
  }

  async deleteEvent(eventId) {
    if (this.db) {
      await this.db.query('UPDATE events SET status = $1 WHERE id = $2', ['deleted', eventId])
    }

    // Удаляем из кеша
    if (this.localCache.has(eventId)) {
      const metadata = this.cacheMetadata.get(eventId)
      this.currentCacheSize -= metadata?.size || 0
      this.localCache.delete(eventId)
      this.cacheMetadata.delete(eventId)
    }

    // Уведомляем пиров
    await this.notifyPeers('EVENT_DELETED', { id: eventId })

    // Уведомляем клиентов
    this.broadcastToClients('EVENT_DELETED', { id: eventId })
  }

  async likeEvent(eventId, isLiked) {
    let event = this.localCache.get(eventId)

    if (!event) {
      if (this.db) {
        event = await this.loadEventFromDB(eventId)
      }
      if (!event) {
        throw new Error('Event not found')
      }
    }

    const newLikes = isLiked ? event.likes + 1 : Math.max(0, event.likes - 1)

    return await this.updateEvent(eventId, { likes: newLikes })
  }

  // === СИНХРОНИЗАЦИЯ ===

  async syncWithPeers() {
    if (this.peers.length === 0) return

    console.log(`🔄 Starting sync with ${this.peers.length} peers`)

    for (const peer of this.peers) {
      try {
        await this.syncWithPeer(peer)
      } catch (error) {
        console.log(`❌ Sync failed with ${peer.id}:`, error.message)
      }
    }
  }

  async syncWithPeer(peer) {
    // Получаем события от пира
    const response = await fetch(`http://${peer.host}:${peer.port}/api/sync/events?since=${this.lastSyncTime}`, {
      headers: { 'x-peer-token': process.env.PEER_TOKEN || 'default_peer_token' },
      timeout: 5000
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const { events, serverId, timestamp } = await response.json()

    if (events.length > 0) {
      await this.receiveSyncEvents(events, serverId, timestamp)
      console.log(`📥 Synced ${events.length} events from ${serverId}`)
    }
  }

  async receiveSyncEvents(events, fromServerId, timestamp) {
    for (const event of events) {
      const existing = this.localCache.get(event.id)

      if (!existing || existing.version < event.version) {
        // Новое событие или более новая версия
        if (this.db) {
          await this.saveEventToDB(event)  // ← ИСПРАВЬ: было updatedEvent
        }
        this.addToCache(event.id, event)

        // Уведомляем клиентов
        this.broadcastToClients(existing ? 'EVENT_UPDATED' : 'EVENT_CREATED', event)
      }
    }

    this.lastSyncTime = Math.max(this.lastSyncTime, timestamp)
  }

  getEventsForSync(since, limit) {
    const sinceTime = parseInt(since) || 0
    const events = Array.from(this.localCache.values())
      .filter(event => new Date(event.updatedAt).getTime() > sinceTime)
      .slice(0, limit)

    return events
  }

  async notifyPeers(eventType, eventData) {
    // Мгновенное уведомление через Redis
    if (this.redis) {
      await this.redis.publish('events_channel', JSON.stringify({
        type: eventType,
        data: eventData,
        fromServerId: this.serverId,
        timestamp: Date.now()
      }))
    }
  }

  handlePeerNotification(notification) {
    const { type, data, fromServerId } = notification

    if (fromServerId === this.serverId) return // Игнорируем свои уведомления

    // Обрабатываем уведомление от пира
    switch (type) {
      case 'EVENT_CREATED':
      case 'EVENT_UPDATED':
        this.addToCache(data.id, data)
        this.broadcastToClients(type, data)
        break

      case 'EVENT_DELETED':
        if (this.localCache.has(data.id)) {
          const metadata = this.cacheMetadata.get(data.id)
          this.currentCacheSize -= metadata?.size || 0
          this.localCache.delete(data.id)
          this.cacheMetadata.delete(data.id)
        }
        this.broadcastToClients(type, data)
        break
    }
  }

  // === БАЗЫ ДАННЫХ ===

  async initializeDatabase() {
    try {
      await this.db.query(`
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          description TEXT NOT NULL,
          author_id TEXT NOT NULL,
          author_name TEXT NOT NULL,
          city TEXT,
          category TEXT,
          likes INTEGER DEFAULT 0,
          created_at TIMESTAMP DEFAULT NOW(),
          updated_at TIMESTAMP DEFAULT NOW(),
          status TEXT DEFAULT 'active',
          server_id TEXT,
          version INTEGER DEFAULT 1
        )
      `)
      console.log('✅ Database initialized')
    } catch (error) {
      console.error('❌ Database initialization failed:', error)
    }
  }

  async saveEventToDB(event) {
    await this.db.query(`
      INSERT INTO events (id, title, description, author_id, author_name, city, category, likes, created_at, updated_at, status, server_id, version)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
      ON CONFLICT (id) DO UPDATE SET
        title = $2, description = $3, author_id = $4, author_name = $5,
        city = $6, category = $7, likes = $8, updated_at = $10, 
        status = $11, server_id = $12, version = $13
    `, [
      event.id, event.title, event.description, event.authorId,
      event.author.fullName, event.city, event.category, event.likes,
      event.createdAt, event.updatedAt, event.status, event.serverId, event.version
    ])
  }

  async loadEventFromDB(eventId) {
    const result = await this.db.query('SELECT * FROM events WHERE id = $1', [eventId])
    return result.rows[0] ? this.formatEventFromDB(result.rows[0]) : null
  }

  formatEventFromDB(row) {
    return {
      id: row.id,
      title: row.title,
      description: row.description,
      authorId: row.author_id,
      author: { fullName: row.author_name },
      city: row.city || '',
      category: row.category || '',
      likes: row.likes || 0,
      createdAt: row.created_at.toISOString(),
      updatedAt: row.updated_at.toISOString(),
      status: row.status,
      serverId: row.server_id,
      version: row.version || 1
    }
  }

  // === ПОИСК И ФИЛЬТРАЦИЯ ===

  async getEventsFromCache({ page, limit, search, city, category }) {
    let events = Array.from(this.localCache.values())
      .filter(event => event.status === 'active')

    // Фильтрация
    if (search) {
      const searchLower = search.toLowerCase()
      events = events.filter(event =>
        event.title.toLowerCase().includes(searchLower) ||
        event.description.toLowerCase().includes(searchLower)
      )
    }

    if (city) {
      events = events.filter(event => event.city === city)
    }

    if (category) {
      events = events.filter(event => event.category === category)
    }

    // Сортировка по дате создания
    events.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))

    // Пагинация
    const offset = (page - 1) * limit
    return events.slice(offset, offset + limit)
  }

  // === ANTI-SLEEP И МОНИТОРИНГ ===

  async performAntiSleepPings() {
    // Пингуем себя
    try {
      await fetch(`http://localhost:${this.port}/health`)
      console.log(`🏓 Self-ping successful`)
    } catch (error) {
      console.log(`❌ Self-ping failed:`, error.message)
    }

    // Пингуем пиров
    await this.pingAllPeers()
  }

  async pingAllPeers() {
    for (const peer of this.peers) {
      try {
        await this.pingPeer(peer, this.isLeader)
      } catch (error) {
        console.log(`💔 Peer ${peer.id} unreachable`)
      }
    }
  }

  async pingPeer(peer, isLeader = false) {
    const response = await fetch(`http://${peer.host}:${peer.port}/api/peer/ping`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-peer-token': process.env.PEER_TOKEN || 'default_peer_token'
      },
      body: JSON.stringify({
        fromServerId: this.serverId,
        isLeader,
        timestamp: Date.now()
      }),
      timeout: 3000
    })

    return await response.json()
  }

  async sendToTelegram(event) {
    if (!this.telegramBot || !this.telegramGroupId) return

    const message = `🎯 ${event.title}\n\n${event.description}\n\n📍 ${event.city}\n👤 ${event.author.fullName}`

    try {
      await this.telegramBot.sendMessage(this.telegramGroupId, message)
      console.log(`📤 Sent to Telegram: ${event.title}`)
    } catch (error) {
      console.error('Telegram send error:', error)
    }
  }

  cleanupCache() {
    const targetSize = this.maxCacheSize * 0.8 // Очищаем до 80% от лимита

    if (this.currentCacheSize > targetSize) {
      const neededSpace = this.currentCacheSize - targetSize
      this.evictLRUEvents(neededSpace)
      console.log(`🧹 Cache cleanup: freed ${this.formatBytes(neededSpace)}`)
    }
  }
}

// === ЗАПУСК СЕРВЕРА ===

const server = new DistributedEventServer()

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('🛑 Graceful shutdown...')
  server.db.end()
  if (server.redis) server.redis.quit()
  process.exit(0)
})

process.on('SIGINT', () => {
  console.log('🛑 Interrupted, shutting down...')
  server.db.end()
  if (server.redis) server.redis.quit()
  process.exit(0)
})

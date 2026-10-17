# Guía para entender y explicar el proyecto

## Qué problema resuelve

Una incidencia pasa por estados y dos personas pueden intentar cambiarla a la vez. La API valida el cambio, lo guarda y conserva un historial. No representa experiencia laboral: es un proyecto de portfolio para practicar backend.

## Ruta de lectura

1. `models.py`: contratos de entrada y salida. Pydantic rechaza títulos vacíos, prioridades inválidas, versiones incorrectas y campos desconocidos.
2. `api.py`: FastAPI convierte una petición HTTP en una llamada a la capa de persistencia. La dependencia de autenticación protege todas las rutas de datos.
3. `repository.py`: SQL parametrizado y transacciones. Cada operación abre su conexión y la cierra incluso si falla.
4. `test_api.py`: observa el comportamiento público y los casos en los que una escritura debe rechazarse.

## Una petición paso a paso

`POST /api/v1/tickets` → comprobar clave → validar JSON → insertar incidencia y evento → confirmar transacción → devolver JSON y código 201.

El código HTTP comunica el resultado. Un 422 significa entrada inválida; un 409 significa que la petición entra en conflicto con el estado actual. Son problemas diferentes y el cliente puede reaccionar de forma distinta.

## El caso importante: dos ediciones simultáneas

Ambos clientes leen la versión 1. El primero cambia el estado y guarda la versión 2. El segundo todavía envía `expected_version: 1`: recibe 409. Así no sobrescribe una decisión que no había visto.

`BEGIN IMMEDIATE` obtiene el bloqueo de escritura antes de leer la versión. La versión permite detectar el conflicto de negocio; el bloqueo evita una carrera dentro de SQLite. Son mecanismos complementarios.

## Qué significa atomicidad aquí

La actualización del ticket y el registro del evento están en una sola transacción. Si falla la inserción del evento, también se deshace el cambio de estado. La prueba `test_audit_failure_rolls_back_status` fuerza ese fallo para comprobarlo.

## Preguntas para practicar

- ¿Por qué no interpolamos el título directamente dentro del SQL?
- ¿Por qué no aceptamos cualquier salto de estado?
- ¿Qué diferencia hay entre autenticación y autorización? ¿Qué limita usar una sola clave?
- ¿Por qué la paginación necesita un orden estable?
- ¿Qué pasaría si la clave estuviera escrita en el código o en un commit?
- ¿Por qué una tabla de eventos no es automáticamente un registro inviolable?

## Siguientes ejercicios

1. Añade un filtro por prioridad y una prueba que combine prioridad y estado.
2. Añade una ruta para editar el título, manteniendo la comprobación de versión y el historial.
3. Añade comentarios a las incidencias sin permitir modificar el historial anterior.
4. Diseña roles de lector y agente; explica por qué no basta con esconder botones en una interfaz.

Antes de presentarlo en una entrevista, ejecuta la demo, provoca un 409 y explica una prueba sin leerla literalmente. Describe con precisión las partes que comprendes y las que estás aprendiendo.

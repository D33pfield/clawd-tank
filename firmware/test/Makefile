# firmware/test/Makefile
CC = gcc
CFLAGS = -Wall -Wextra -fsanitize=address,undefined -Werror -I../main -Istubs
LDFLAGS = -fsanitize=address,undefined

test: test_notification test_config_store test_rle_decode test_daynight
	./test_notification
	./test_config_store
	./test_rle_decode
	./test_daynight

test_notification: test_notification.c ../main/notification.c
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

test_config_store: test_config_store.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

test_rle_decode: test_rle_decode.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

test_daynight: test_daynight.c ../main/daynight.c
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

clean:
	rm -f test_notification test_config_store test_rle_decode test_daynight

.PHONY: test clean

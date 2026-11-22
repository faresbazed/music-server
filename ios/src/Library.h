#import <Foundation/Foundation.h>

extern NSString *const LibraryDidChange;
NSString *Text(id value, NSString *fallback);
NSString *MatchedID(NSDictionary *track);
NSString *AudioExtension(id value);

// All mutations and change notifications occur on the main queue in the app.
@interface Library : NSObject {
    NSURL *_root;
    NSMutableDictionary *_byKey;
}
- (instancetype)initWithDirectory:(NSURL *)directory;
- (BOOL)reload:(NSError **)error;
- (NSArray *)records;
- (BOOL)containsTrack:(NSDictionary *)track origin:(NSString *)origin;
- (BOOL)saveFile:(NSURL *)temporary track:(NSDictionary *)track origin:(NSString *)origin error:(NSError **)error;
- (BOOL)removeRecord:(NSDictionary *)record error:(NSError **)error;
- (NSURL *)audioURL:(NSDictionary *)record;
@end
